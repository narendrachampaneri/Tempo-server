"""Quota manager: tracks free-tier usage so Tempo routes around limits before a 429.

Counts requests and tokens per (provider, key, model) in a sliding one-minute window and per day
(in the provider's reset time zone, UTC unless the registry says otherwise), plus provider-wide
pools (OpenRouter's free models share one daily quota). Daily counts are persisted in the store
so a restart does not forget them. Rate-limit headers reported by providers override the counts
when they are lower (other clients may share the same key).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo

from tempo.registry import Registry
from tempo.store import Store
from tempo.types import ModelInfo

MINUTE = 60.0


@dataclass(frozen=True)
class QuotaLeft:
    """Remaining free quota; ``None`` means no known limit."""

    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    tpd: int | None = None
    rpmonth: int | None = None  # requests left this calendar month (Cohere's trial key)
    tpmonth: int | None = None  # tokens left this month, as a provider's headers report it

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)


@lru_cache(maxsize=32)
def _zone(name: str) -> tzinfo:
    return UTC if name == "UTC" else ZoneInfo(name)


def _min_known(*values: int | None) -> int | None:
    known = [v for v in values if v is not None]
    return max(0, min(known)) if known else None


class QuotaManager:
    def __init__(
        self,
        registry: Registry,
        store: Store | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.registry = registry
        self.store = store
        self._clock = clock
        self._minute: dict[str, deque[tuple[float, int]]] = {}
        self._day: dict[tuple[str, str], list[int]] = {}
        # bucket -> {"rpm"|"rpd"|"tpm"|"tpd": (remaining, observed_at, day)}
        self._headers: dict[str, dict[str, tuple[int, float, str]]] = {}

    # --- buckets -----------------------------------------------------------------

    @staticmethod
    def model_bucket(model: ModelInfo, key_id: str) -> str:
        return f"{model.provider}:{key_id}:{model.id}"

    @staticmethod
    def provider_bucket(provider: str, key_id: str) -> str:
        return f"{provider}:{key_id}:*"

    def _today(self, provider_id: str) -> str:
        """Today's date where this provider's daily quota resets."""
        provider = self.registry.providers.get(provider_id)
        zone = _zone(provider.day_reset_tz if provider else "UTC")
        return datetime.fromtimestamp(self._clock(), zone).strftime("%Y-%m-%d")

    def seconds_to_day_reset(self, provider_id: str) -> float:
        """Seconds until this provider's daily free quota resets (midnight in its time zone)."""
        from datetime import timedelta

        provider = self.registry.providers.get(provider_id)
        zone = _zone(provider.day_reset_tz if provider else "UTC")
        now = datetime.fromtimestamp(self._clock(), zone)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1.0, (midnight - now).total_seconds())

    def _month(self, provider_id: str) -> str:
        return self._today(provider_id)[:7]

    def _month_usage(self, bucket: str) -> int:
        """Requests this month: the persisted daily counts of this month, added up."""
        month = self._month(self._provider_of(bucket))
        if self.store is not None:
            rows = self.store.query(
                "SELECT day, requests FROM quota_days WHERE bucket = ? AND day LIKE ?",
                (bucket, month + "-%"),
            )
            counts = {r["day"]: r["requests"] for r in rows}
        else:
            counts = {}
        for (b, day), (requests, _) in self._day.items():
            if b == bucket and day.startswith(month):
                counts[day] = requests
        return sum(counts.values())

    @staticmethod
    def _provider_of(bucket: str) -> str:
        return bucket.split(":", 1)[0]

    def _minute_usage(self, bucket: str) -> tuple[int, int]:
        window = self._minute.get(bucket)
        if not window:
            return 0, 0
        cutoff = self._clock() - MINUTE
        while window and window[0][0] <= cutoff:
            window.popleft()
        return len(window), sum(tokens for _, tokens in window)

    def _day_usage(self, bucket: str) -> tuple[int, int]:
        key = (bucket, self._today(self._provider_of(bucket)))
        if key not in self._day:
            loaded = self.store.quota_day(*key) if self.store else (0, 0)
            self._day[key] = list(loaded)
        requests, tokens = self._day[key]
        return requests, tokens

    def _header(self, bucket: str, name: str) -> int | None:
        entry = self._headers.get(bucket, {}).get(name)
        if entry is None:
            return None
        value, observed_at, day = entry
        if name in ("rpm", "tpm") and self._clock() - observed_at > MINUTE:
            return None
        if name in ("rpd", "tpd") and day != self._today(self._provider_of(bucket)):
            return None
        if name in ("rpmonth", "tpmonth") and day[:7] != self._month(self._provider_of(bucket)):
            return None
        return value

    # --- queries -------------------------------------------------------------------

    def left(self, model: ModelInfo, key_id: str = "server") -> QuotaLeft:
        provider = self.registry.providers[model.provider]
        if provider.local:
            return QuotaLeft()
        mb = self.model_bucket(model, key_id)
        pb = self.provider_bucket(model.provider, key_id)
        m_min_req, m_min_tok = self._minute_usage(mb)
        m_day_req, m_day_tok = self._day_usage(mb)
        p_min_req, _ = self._minute_usage(pb)
        p_day_req, _ = self._day_usage(pb)

        def minus(limit: int | None, used: int) -> int | None:
            return None if limit is None else limit - used

        return QuotaLeft(
            rpm=_min_known(
                minus(model.free_rpm, m_min_req),
                minus(provider.shared_rpm, p_min_req),
                self._header(mb, "rpm"),
            ),
            rpd=_min_known(
                minus(model.free_rpd, m_day_req),
                minus(provider.shared_rpd, p_day_req),
                self._header(mb, "rpd"),
            ),
            tpm=_min_known(minus(model.free_tpm, m_min_tok), self._header(mb, "tpm")),
            tpd=_min_known(minus(model.free_tpd, m_day_tok), self._header(mb, "tpd")),
            rpmonth=_min_known(
                minus(provider.shared_rpmonth, self._month_usage(pb))
                if provider.shared_rpmonth
                else None,
                self._header(mb, "rpmonth"),
            ),
            tpmonth=self._header(mb, "tpmonth"),
        )

    def blocked_reason(
        self, model: ModelInfo, key_id: str = "server", tokens: int = 0, reserve: float = 0.0
    ) -> str | None:
        """Why this model cannot take a request of ``tokens`` now. ``reserve`` keeps that share
        of each daily free limit untouched (for background jobs such as `tempo-server collect`)."""
        left = self.left(model, key_id)
        if left.rpmonth == 0:
            return "free requests/month used up"
        if left.tpmonth is not None and tokens > left.tpmonth:
            return "free tokens/month used up"
        if left.rpd == 0:
            return "free requests/day used up"
        if left.tpd is not None and tokens > left.tpd:
            return "free tokens/day used up"
        if reserve > 0 and not self.registry.providers[model.provider].local:
            provider = self.registry.providers[model.provider]
            rpd_limit = _min_known(model.free_rpd, provider.shared_rpd)
            if rpd_limit and left.rpd is not None and left.rpd <= reserve * rpd_limit:
                return "rest of today's free requests kept for users"
            tpd_kept = reserve * (model.free_tpd or 0)
            if model.free_tpd and left.tpd is not None and left.tpd - tokens < tpd_kept:
                return "rest of today's free tokens kept for users"
        if left.rpm == 0:
            return "free requests/min used up"
        if left.tpm is not None and tokens > left.tpm:
            return "free tokens/min used up"
        return None

    # --- updates -------------------------------------------------------------------

    def record(self, model: ModelInfo, key_id: str = "server", tokens: int = 0) -> None:
        if self.registry.providers[model.provider].local:
            return
        now = self._clock()
        day = self._today(model.provider)
        for bucket in (
            self.model_bucket(model, key_id),
            self.provider_bucket(model.provider, key_id),
        ):
            self._minute.setdefault(bucket, deque()).append((now, tokens))
            self._day_usage(bucket)  # make sure today's persisted count is loaded first
            counts = self._day[(bucket, day)]
            counts[0] += 1
            counts[1] += tokens
            if self.store:
                self.store.quota_add(bucket, day, 1, tokens)

    def observe_headers(
        self, model: ModelInfo, key_id: str, headers: Mapping[str, str] | None
    ) -> None:
        """Read x-ratelimit-* headers (LiteLLM prefixes them with llm_provider-).

        ``remaining`` headers correct the counters. ``limit`` headers are the provider's own
        statement of the free limit: they fill in a limit the registry does not know, and
        replace a hand-entered one (rule: limits are read live wherever a provider sends them).
        """
        if not headers:
            return
        provider = self.registry.providers[model.provider]
        requests = "rpd" if provider.requests_header_window == "day" else "rpm"
        windows = {
            "requests-day": "rpd",
            "requests-minute": "rpm",
            "req-minute": "rpm",
            "requests-month": "rpmonth",
            "req-month": "rpmonth",
            "requests": requests,
            "tokens-minute": "tpm",
            "tokens": "tpm",
            "tokens-day": "tpd",
            "tokens-month": "tpmonth",
        }
        limit_fields = {"rpm": "free_rpm", "rpd": "free_rpd", "tpm": "free_tpm", "tpd": "free_tpd"}
        bucket = self.model_bucket(model, key_id)
        now, day = self._clock(), self._today(model.provider)
        learned = False
        for raw_name, raw_value in headers.items():
            name = str(raw_name).lower().removeprefix("llm_provider-")
            if name == "ratelimitbysize-remaining":  # Mistral: tokens left this minute
                name = "x-ratelimit-remaining-tokens-minute"
            if not name.startswith(("x-ratelimit-remaining-", "x-ratelimit-limit-")):
                continue
            which, _, window = name.removeprefix("x-ratelimit-").partition("-")
            kind = windows.get(window)
            if kind is None:
                continue
            try:
                value = int(float(raw_value))
            except (TypeError, ValueError):
                continue
            if which == "remaining":
                self._headers.setdefault(bucket, {})[kind] = (value, now, day)
            elif kind in limit_fields and value > 0:
                if getattr(model, limit_fields[kind]) != value:
                    setattr(model, limit_fields[kind], value)
                    learned = True
        if learned:
            model.limits_source = "live: response headers"
            model.limits_checked = day
