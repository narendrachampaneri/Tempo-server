"""`tempo collect`: make Laya training data by running openly licensed public questions through
Tempo, slowly and within every free limit.

- One question at a time, at most ``per_minute`` questions a minute.
- Never works around a limit: no extra keys, no retries against a model that said 429. The
  normal quota manager and cool-downs apply, and ``reserve`` keeps that share of each model's
  daily free quota untouched for real users.
- When no model has free quota left, it waits for quota to come back (or stops, with
  ``wait=False``). Progress is stored per item, so a later run resumes where this one stopped.
- Providers whose terms say outputs may not be used for training are left out by default;
  their answers could not be exported anyway.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tempo.datasets import DATASETS, Item, interleave, usable
from tempo.registry import Registry
from tempo.store import Store
from tempo.types import Access

if TYPE_CHECKING:
    from tempo.engine import Engine

COLLECT_USER = "collect"  # questions are logged under this user, apart from real traffic
MAX_ATTEMPTS = 3  # a question that failed this often is left alone
WAIT_S = 15 * 60  # how long to wait when every model's free quota is used up
WAIT_KINDS = {"unavailable", "rate_limit", "quota"}

# Until enough collected questions exist to measure them (see `measured`).
DEFAULT_DECISIONS_PER_QUESTION = 9.0
DEFAULT_REQUESTS_PER_QUESTION = 4.0
DEFAULT_TOKENS_PER_REQUEST = 1500
TARGET_DECISIONS = 7000  # Laya's own fine-tune used 6,000 typed decisions (1,200 cases)


@dataclass
class CollectStats:
    done: int = 0
    failed: int = 0
    skipped: int = 0
    already: int = 0
    waited_s: float = 0.0
    stopped: str = "finished"  # finished | limit | time | exhausted | interrupted


def download(name: str, cache_dir: Path, client: httpx.Client | None = None) -> bytes:
    """The dataset file, downloaded once into ``cache_dir``."""
    dataset = DATASETS[name]
    path = cache_dir / dataset.filename
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        own = client is None
        client = client or httpx.Client(timeout=120, follow_redirects=True)
        try:
            response = client.get(dataset.url)
            response.raise_for_status()
        finally:
            if own:
                client.close()
        partial = path.with_suffix(path.suffix + ".part")
        partial.write_bytes(response.content)
        partial.replace(path)
    return path.read_bytes()


def load_items(
    names: Iterable[str], cache_dir: Path, client: httpx.Client | None = None
) -> dict[str, list[Item]]:
    return {name: list(DATASETS[name].parse(download(name, cache_dir, client))) for name in names}


def default_providers(registry: Registry) -> list[str]:
    """Configured providers whose outputs may end up in training data (terms not "no")."""
    return [
        p.id
        for p in registry.providers.values()
        if registry.is_configured(p.id)
        and p.training_on_outputs != "no"
        and not registry.blocked_for(p.id, "collect")
    ]


def has_yes_models(registry: Registry, provider_id: str) -> bool:
    """Does this provider have a chat model whose outputs may be training data?"""
    return any(
        m.provider == provider_id and m.chat_capable and registry.training_verdict(m) == "yes"
        for m in registry.all()
    )


async def run(
    engine: Engine,
    items: dict[str, list[Item]],
    *,
    limit: int | None = None,
    per_minute: float = 2.0,
    reserve: float = 0.5,
    yes_only: bool = False,
    providers: list[str] | None = None,
    wait: bool = True,
    max_minutes: float | None = None,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CollectStats:
    stats = CollectStats()
    providers = providers if providers is not None else default_providers(engine.registry)
    blocked = [p for p in providers if engine.registry.blocked_for(p, "collect")]
    if blocked:
        raise ValueError(f"not allowed for tempo collect: {', '.join(blocked)}")
    gap = 60.0 / per_minute if per_minute > 0 else 0.0
    options = engine.options(
        mode="auto",
        allow_providers=providers,
        quota_reserve=reserve,
        use_cache=False,
        live=False,
        quota_budget=min(engine.settings.quota_budget, 6),
        access=Access(user_id=COLLECT_USER),
        training_only=yes_only,
    )
    deadline = clock() + max_minutes * 60 if max_minutes else None
    try:
        await _run_items(
            engine, items, stats, limit, gap, options, wait, say, sleep, clock, deadline
        )
    finally:
        if engine.laya is not None:  # shadow predictions still running reach the log first
            await engine.laya.drain()
    return stats


async def _run_items(
    engine: Engine,
    items: dict[str, list[Item]],
    stats: CollectStats,
    limit: int | None,
    gap: float,
    options: Any,
    wait: bool,
    say: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], float],
    deadline: float | None = None,
) -> None:
    store = engine.store
    next_start = clock()
    for name, item in interleave(items):
        record = store.collect_item(name, item.item_id)
        if record is not None and (
            record["status"] in ("done", "skipped")
            or (record["status"] == "error" and record["attempts"] >= MAX_ATTEMPTS)
        ):
            stats.already += 1
            continue
        reason = usable(item.text)
        if reason:
            store.collect_mark(name, item.item_id, "skipped", note=reason)
            stats.skipped += 1
            continue
        if limit is not None and stats.done + stats.failed >= limit:
            stats.stopped = "limit"
            return
        if deadline is not None and max(clock(), next_start) >= deadline:
            stats.stopped = "time"  # the next question would start after the time limit
            return
        while True:
            delay = next_start - clock()
            if delay > 0:
                await sleep(delay)
            next_start = clock() + gap
            result = await engine.complete([{"role": "user", "content": item.text}], options)
            if result.error is None:
                store.collect_mark(
                    name, item.item_id, "done", question_id=result.question_id, attempt=True
                )
                stats.done += 1
                say(
                    f"{stats.done:>5} done · {name} {item.item_id} · {result.model} · "
                    f"{result.stages} stages · {result.requests} requests · {result.stop_reason}"
                )
                break
            if result.error_kind in WAIT_KINDS:
                if not wait:
                    say(f"No free quota left ({result.error}). Stopping; run again to resume.")
                    stats.stopped = "exhausted"
                    return
                if deadline is not None and clock() + WAIT_S >= deadline:
                    say(f"No free quota left ({result.error}). Time is up; run again to resume.")
                    stats.stopped = "time"
                    return
                say(f"No free quota left ({result.error}). Waiting {WAIT_S // 60} minutes.")
                await sleep(WAIT_S)
                stats.waited_s += WAIT_S
                continue
            store.collect_mark(
                name,
                item.item_id,
                "error",
                question_id=result.question_id,
                attempt=True,
                note=(result.error or "")[:200],
            )
            stats.failed += 1
            say(f"  failed · {name} {item.item_id} · {result.error}")
            break


# --- how long it will take ------------------------------------------------------------------


@dataclass
class ProviderCapacity:
    provider: str
    label: str
    requests_per_day: float
    configured: bool
    trial: bool
    note: str = ""


@dataclass
class Estimate:
    target_decisions: int
    decisions_per_question: float
    requests_per_question: float
    tokens_per_request: float
    measured_from: int  # collected questions the per-question numbers come from (0 = defaults)
    reserve: float
    providers: list[ProviderCapacity] = field(default_factory=list)
    per_minute: float = 2.0

    @property
    def questions_needed(self) -> int:
        return math.ceil(self.target_decisions / self.decisions_per_question)

    def questions_per_day(self, configured_only: bool = True) -> float:
        requests = sum(
            p.requests_per_day for p in self.providers if p.configured or not configured_only
        )
        pace = self.per_minute * 60 * 24 if self.per_minute > 0 else math.inf
        return min(requests / self.requests_per_question, pace)

    def days(self, configured_only: bool = True) -> float | None:
        per_day = self.questions_per_day(configured_only)
        return self.questions_needed / per_day if per_day > 0 else None


def measured(store: Store) -> tuple[int, float, float, float] | None:
    """(questions, decisions/question, requests/question, tokens/request) from collected runs."""
    rows = store.query(
        "SELECT q.id, q.requests_used FROM questions q JOIN collect_items c "
        "ON c.question_id = q.id WHERE c.status = 'done' AND q.error IS NULL"
    )
    if len(rows) < 20:
        return None
    ids = [r["id"] for r in rows]
    marks = ",".join("?" for _ in ids)
    decisions = store.query(
        f"SELECT COUNT(*) AS n FROM decisions WHERE question_id IN ({marks})", ids
    )[0]["n"]
    tokens = store.query(
        "SELECT AVG(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS t FROM calls "
        f"WHERE status = 'ok' AND question_id IN ({marks})",
        ids,
    )[0]["t"]
    requests = sum(r["requests_used"] or 0 for r in rows) / len(rows)
    return len(rows), decisions / len(rows), max(requests, 1.0), float(tokens or 0) or 1.0


def estimate(
    registry: Registry,
    store: Store | None = None,
    *,
    target_decisions: int = TARGET_DECISIONS,
    reserve: float = 0.5,
    per_minute: float = 2.0,
    providers: list[str] | None = None,
    yes_only: bool = False,
) -> Estimate:
    stats = measured(store) if store is not None else None
    n, per_q, req_q, tok_r = stats or (
        0,
        DEFAULT_DECISIONS_PER_QUESTION,
        DEFAULT_REQUESTS_PER_QUESTION,
        DEFAULT_TOKENS_PER_REQUEST,
    )
    result = Estimate(target_decisions, per_q, req_q, tok_r, n, reserve, per_minute=per_minute)
    wanted = set(providers) if providers is not None else None
    for provider in registry.providers.values():
        if provider.training_on_outputs == "no" or provider.id == "mock":
            continue
        if provider.blocked_for and "collect" in provider.blocked_for:
            continue
        if wanted is not None and provider.id not in wanted:
            continue
        if yes_only and not has_yes_models(registry, provider.id):
            continue
        if provider.local:
            # No free quota to count: only the pace limits a local run.
            installed = any(
                m.provider == provider.id and m.installed is not False and m.chat_capable
                for m in registry.all()
            )
            pace = per_minute * 60 * 24 if per_minute > 0 else 0.0
            result.providers.append(
                ProviderCapacity(
                    provider=provider.id,
                    label=provider.label,
                    requests_per_day=pace * req_q if installed else 0.0,
                    configured=registry.is_configured(provider.id),
                    trial=False,
                    note="local: limited only by --per-minute and your CPU",
                )
            )
            continue
        per_day = 0.0
        for model in registry.all():
            if model.provider != provider.id or model.listed is False:
                continue
            if not model.chat_capable:
                continue
            limits = [float(x) for x in (model.free_rpd,) if x]
            if model.free_tpd:
                limits.append(model.free_tpd / tok_r)
            if not limits and model.free_rpm:
                limits.append(model.free_rpm * 60.0 * 24)
            per_day += min(limits) if limits else math.inf  # no per-model limit known
        if provider.shared_rpd:
            per_day = min(per_day, float(provider.shared_rpd))
        if math.isinf(per_day):
            per_day = 0.0  # nothing known to plan with
        result.providers.append(
            ProviderCapacity(
                provider=provider.id,
                label=provider.label,
                requests_per_day=per_day * (1 - reserve),
                configured=registry.is_configured(provider.id),
                trial=provider.free_tier == "trial",
                note=provider.free_tier_note or "",
            )
        )
    return result


def describe(est: Estimate) -> list[str]:
    source = (
        f"measured on {est.measured_from} collected questions"
        if est.measured_from
        else "assumed until 20 questions have been collected"
    )
    lines = [
        f"Target: {est.target_decisions:,} labelled decisions "
        "(Laya's own fine-tune used 6,000 from 1,200 cases).",
        f"Per question: {est.decisions_per_question:.1f} decisions, "
        f"{est.requests_per_question:.1f} free requests, "
        f"{est.tokens_per_request:,.0f} tokens per request ({source}).",
        f"So about {est.questions_needed:,} questions.",
        f"Free capacity per day, keeping {est.reserve:.0%} of each daily limit for users:",
    ]
    for p in est.providers:
        flags = [] if p.configured else ["no key"]
        if p.trial:
            flags.append("trial credits only")
        lines.append(
            f"  {p.label:<26} {p.requests_per_day:8,.0f} requests/day"
            + (f"  ({', '.join(flags)})" if flags else "")
        )
    for configured_only, label in ((True, "with the keys you have"), (False, "with every key")):
        days = est.days(configured_only)
        per_day = est.questions_per_day(configured_only)
        if days is None:
            lines.append(f"{label.capitalize()}: no free capacity.")
        else:
            lines.append(
                f"{label.capitalize()}: about {per_day:,.0f} questions/day, so {days:,.1f} days."
            )
    return lines


def summary_counts(store: Store) -> dict[str, Any]:
    return store.collect_counts()
