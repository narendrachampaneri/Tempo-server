from conftest import make_registry

from tempo.health import HealthTracker
from tempo.quota import QuotaManager
from tempo.registry import Registry
from tempo.router import Router, scarcity
from tempo.store import Store
from tempo.types import ModelInfo, ProviderInfo, QueryProfile


class Clock:
    def __init__(self, t: float = 1_790_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def pool_registry() -> Registry:
    providers = {
        "pool": ProviderInfo(
            id="pool", label="Pool", key_env="POOL_KEY", shared_rpd=3, shared_rpm=2
        ),
        "day": ProviderInfo(id="day", label="Day", key_env="DAY_KEY", requests_header_window="day"),
    }
    models = [
        ModelInfo(id="pool/a", provider="pool", name="a"),
        ModelInfo(id="pool/b", provider="pool", name="b"),
        ModelInfo(
            id="day/m",
            provider="day",
            name="m",
            free_rpm=2,
            free_rpd=100,
            free_tpm=1000,
            free_tpd=5000,
        ),
    ]
    return Registry(providers, models, env={"POOL_KEY": "x", "DAY_KEY": "y"})


def test_minute_window_slides_and_day_counts():
    clock = Clock()
    registry = pool_registry()
    quota = QuotaManager(registry, clock=clock)
    m = registry.get("day/m")
    quota.record(m, tokens=400)
    quota.record(m, tokens=400)
    left = quota.left(m)
    assert (left.rpm, left.rpd, left.tpm, left.tpd) == (0, 98, 200, 4200)
    assert quota.blocked_reason(m) == "free requests/min used up"
    clock.t += 61
    assert quota.left(m).rpm == 2 and quota.left(m).rpd == 98
    assert quota.blocked_reason(m, tokens=5000) == "free tokens/day used up"


def test_provider_pool_is_shared_across_models():
    clock = Clock()
    registry = pool_registry()
    quota = QuotaManager(registry, clock=clock)
    a, b = registry.get("pool/a"), registry.get("pool/b")
    quota.record(a)
    quota.record(b)
    assert quota.left(a).rpm == 0 and quota.left(b).rpm == 0
    clock.t += 61
    quota.record(a)
    assert quota.left(b).rpd == 0
    assert quota.blocked_reason(b) == "free requests/day used up"


def test_each_key_has_its_own_bucket():
    registry = pool_registry()
    quota = QuotaManager(registry, clock=Clock())
    a = registry.get("pool/a")
    quota.record(a, "server")
    quota.record(a, "server")
    assert quota.left(a, "server").rpm == 0
    assert quota.left(a, "key:alice").rpm == 2


def test_day_counts_survive_restart_and_reset_next_day(tmp_path):
    clock = Clock()
    registry = pool_registry()
    store = Store(tmp_path / "q.db")
    QuotaManager(registry, store, clock=clock).record(registry.get("day/m"), tokens=10)
    fresh = QuotaManager(registry, Store(tmp_path / "q.db"), clock=clock)
    assert fresh.left(registry.get("day/m")).rpd == 99
    clock.t += 24 * 3600
    assert fresh.left(registry.get("day/m")).rpd == 100


def test_headers_override_counts_when_lower():
    clock = Clock()
    registry = pool_registry()
    quota = QuotaManager(registry, clock=clock)
    m = registry.get("day/m")
    quota.observe_headers(
        m,
        "server",
        {
            "llm_provider-x-ratelimit-remaining-requests": "3",  # this provider counts the day
            "x-ratelimit-remaining-tokens": "150",
            "x-ratelimit-remaining-requests-minute": "not-a-number",
        },
    )
    left = quota.left(m)
    assert left.rpd == 3 and left.tpm == 150 and left.rpm == 2
    clock.t += 61  # minute headers expire; day headers last until the day changes
    assert quota.left(m).tpm == 1000 and quota.left(m).rpd == 3


def test_local_models_are_unlimited():
    registry = make_registry()
    quota = QuotaManager(registry, clock=Clock())
    tiny = registry.get("local/tiny")
    quota.record(tiny, tokens=10_000)
    assert set(quota.left(tiny).as_dict().values()) == {None}


def test_router_skips_exhausted_models_and_uses_remaining_quota():
    registry = make_registry()
    clock = Clock()
    quota = QuotaManager(registry, clock=clock)
    router = Router(registry, HealthTracker(), quota)
    profile = QueryProfile(
        task="chat",
        complexity=0.1,
        script="latin",
        needs=[],
        input_tokens=10,
        est_output_tokens=100,
    )
    strong = registry.get("alpha/strong")  # 20 requests/day
    before = router.candidate(strong, profile).scarcity
    for _ in range(19):
        quota.record(strong)
    after = router.candidate(strong, profile).scarcity
    assert after > before
    assert after == scarcity(strong, 1)
    quota.record(strong)
    result = router.rank(profile)
    assert result.skipped["free requests/day used up"] == ["alpha/strong"]


def test_daily_quota_resets_in_the_providers_time_zone():
    from datetime import UTC, datetime

    import pytest

    providers = {
        "pac": ProviderInfo(
            id="pac", label="Pacific", key_env="PAC_KEY", day_reset_tz="America/Los_Angeles"
        )
    }
    model = ModelInfo(id="pac/m", provider="pac", name="m", free_rpd=10)
    registry = Registry(providers, [model], env={"PAC_KEY": "x"})
    # 06:30 UTC on 28 Sep is still 27 Sep in California; 07:30 UTC is past midnight there.
    clock = Clock(datetime(2026, 9, 28, 6, 30, tzinfo=UTC).timestamp())
    quota = QuotaManager(registry, clock=clock)
    quota.record(model)
    quota.record(model)
    assert quota.left(model).rpd == 8
    clock.t += 30 * 60  # 07:00 UTC = 00:00 PDT
    assert quota.left(model).rpd == 10
    with pytest.raises(ValueError):
        ProviderInfo(id="bad", label="Bad", day_reset_tz="Mars/Olympus")


def test_monthly_pool_and_limits_read_from_headers(tmp_path):
    from tempo.store import Store

    providers = {
        "month": ProviderInfo(id="month", label="Month", key_env="M", shared_rpmonth=3),
        "hdr": ProviderInfo(id="hdr", label="Hdr", key_env="H", requests_header_window="day"),
    }
    models = [
        ModelInfo(id="month/a", provider="month", name="a"),
        ModelInfo(id="hdr/a", provider="hdr", name="a", free_rpd=50),
    ]
    registry = Registry(providers, models, env={"M": "x", "H": "x"})
    clock = Clock()
    quota = QuotaManager(registry, Store(tmp_path / "q.db"), clock=clock)
    month = registry.get("month/a")
    for _ in range(3):
        quota.record(month)
        clock.t += 86400  # spread over days: the month still adds up
    assert quota.left(month).rpmonth == 0
    assert quota.blocked_reason(month) == "free requests/month used up"

    hdr = registry.get("hdr/a")
    quota.observe_headers(
        hdr,
        "server",
        {
            "llm_provider-x-ratelimit-limit-requests": "14400",  # Groq: requests per day
            "llm_provider-x-ratelimit-limit-tokens": "6000",  # tokens per minute
            "x-ratelimit-remaining-tokens-month": "999",  # Mistral-style monthly tokens
        },
    )
    assert hdr.free_rpd == 14400 and hdr.free_tpm == 6000
    assert hdr.limits_source == "live: response headers" and hdr.limits_checked
    assert quota.left(hdr).tpmonth == 999
    assert quota.blocked_reason(hdr, tokens=5000) == "free tokens/month used up"
