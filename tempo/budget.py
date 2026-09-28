"""Free capacity per provider: how many requests a day the free tiers give, and what is left
today. Used by `tempo setup`, `tempo quota`, the web page's quota panel, and the fallback to
local models when every free quota is used up."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from tempo.types import Access, ModelInfo

if TYPE_CHECKING:
    from tempo.engine import Engine
    from tempo.registry import Registry


@dataclass
class ProviderQuota:
    provider: str
    label: str
    local: bool = False
    per_day: int | None = None  # free requests a day (None: no daily limit Tempo knows)
    left_today: int | None = None
    per_minute: int | None = None
    per_month: int | None = None
    resets_in_s: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def used_up(self) -> bool:
        return self.left_today == 0


def _chat_models(registry: Registry, provider_id: str) -> list[ModelInfo]:
    return [
        m
        for m in registry.all()
        if m.provider == provider_id
        and m.chat_capable
        and m.listed is not False
        and m.installed is not False
    ]


def daily_capacity(registry: Registry, provider_id: str) -> tuple[int | None, int | None, str]:
    """(requests a day, requests a minute, note) the free tier gives for chat models."""
    provider = registry.providers[provider_id]
    models = _chat_models(registry, provider_id)
    per_day = sum(m.free_rpd for m in models if m.free_rpd) or None
    if provider.shared_rpd:
        per_day = min(per_day, provider.shared_rpd) if per_day else provider.shared_rpd
    per_minute = provider.shared_rpm or max((m.free_rpm or 0 for m in models), default=0) or None
    note = ""
    if provider.shared_rpmonth:
        note = f"{provider.shared_rpmonth:,} requests a month"
        per_day = per_day or provider.shared_rpmonth // 30
    elif per_day is None and provider.free_limit_note:
        note = provider.free_limit_note.split(";")[0]
    elif per_day is None and models and any(m.free_tpd for m in models):
        note = "limited by tokens per day"
    return per_day, per_minute, note


def quota_view(engine: Engine, access: Access | None = None) -> list[ProviderQuota]:
    """What each configured provider has left today, for this caller's keys."""
    access = access or engine.access_for("local")
    rows: list[ProviderQuota] = []
    for provider in engine.registry.providers.values():
        if provider.id == "mock" or not engine.registry.is_configured(provider.id, access):
            continue
        models = _chat_models(engine.registry, provider.id)
        if provider.local:
            if models:
                rows.append(
                    ProviderQuota(provider.id, provider.label, local=True, note="no limit (local)")
                )
            continue
        per_day, per_minute, note = daily_capacity(engine.registry, provider.id)
        key_id = access.key_id(provider.id)
        lefts = [engine.quota.left(m, key_id) for m in models]
        left_today: int | None = None
        if provider.shared_rpd:
            left_today = min((q.rpd for q in lefts if q.rpd is not None), default=None)
        elif any(q.rpd is not None for q in lefts):
            left_today = sum(q.rpd for q in lefts if q.rpd is not None)
        month = min((q.rpmonth for q in lefts if q.rpmonth is not None), default=None)
        if month is not None:
            left_today = month if left_today is None else min(left_today, month)
        rows.append(
            ProviderQuota(
                provider.id,
                provider.label,
                per_day=per_day,
                left_today=left_today,
                per_minute=per_minute,
                per_month=provider.shared_rpmonth,
                resets_in_s=engine.quota.seconds_to_day_reset(provider.id),
                note=note,
            )
        )
    return rows


def all_used_up(view: list[ProviderQuota]) -> bool:
    """Every hosted provider with a known daily limit has none left (local ones don't count)."""
    hosted = [q for q in view if not q.local]
    return bool(hosted) and all(q.used_up for q in hosted)


def resets_text(seconds: float | None) -> str:
    if not seconds:
        return "-"
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h {rest // 60:02d}m" if hours else f"{rest // 60}m"
