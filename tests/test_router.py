from conftest import make_registry

from tempo.analyzer import analyze
from tempo.health import HealthTracker
from tempo.router import Router, predicted_quality, scarcity
from tempo.types import QueryProfile


def chat_profile(**overrides) -> QueryProfile:
    base = dict(
        task="chat",
        complexity=0.15,
        script="latin",
        needs=[],
        input_tokens=20,
        est_output_tokens=150,
    )
    return QueryProfile(**{**base, **overrides})


def ranked(router: Router, profile: QueryProfile, **kw) -> list[str]:
    return [c.model.id for c in router.rank(profile, **kw).candidates]


def test_auto_prefers_good_fast_model_with_generous_quota(registry):
    router = Router(registry, HealthTracker())
    assert ranked(router, chat_profile())[0] == "beta/mid"


def test_best_mode_prefers_strongest_model_despite_scarce_quota(registry):
    router = Router(registry, HealthTracker())
    result = router.rank(chat_profile(), mode="best")
    assert result.candidates[0].model.id == "alpha/strong"
    assert "highest predicted quality" in result.candidates[0].why


def test_fast_mode_prefers_a_fast_model(registry):
    router = Router(registry, HealthTracker())
    top = router.rank(chat_profile(), mode="fast").candidates[0]
    assert top.model.id in ("beta/mid", "alpha/small")
    assert top.latency_s < 1.0


def test_candidates_are_sorted_by_utility(registry):
    candidates = Router(registry, HealthTracker()).rank(chat_profile()).candidates
    utilities = [c.utility for c in candidates]
    assert utilities == sorted(utilities, reverse=True)


def test_skips_unconfigured_providers():
    router = Router(make_registry({"BETA_KEY": "b"}), HealthTracker())
    result = router.rank(chat_profile())
    assert ranked(router, chat_profile()) == ["beta/mid"]
    assert result.skipped["provider not configured"] == [
        "alpha/strong",
        "alpha/small",
        "local/tiny",
    ]


def test_private_mode_only_uses_local_models(registry):
    router = Router(registry, HealthTracker())
    assert ranked(router, chat_profile(), mode="private") == ["local/tiny"]
    assert ranked(router, chat_profile(), local_only=True) == ["local/tiny"]


def test_allow_providers_filter(registry):
    router = Router(registry, HealthTracker())
    assert set(ranked(router, chat_profile(), allow_providers=["alpha"])) == {
        "alpha/strong",
        "alpha/small",
    }


def test_context_and_free_tpm_limits(registry):
    router = Router(registry, HealthTracker())
    result = router.rank(chat_profile(input_tokens=7000))
    ids = [c.model.id for c in result.candidates]
    assert "local/tiny" not in ids  # 4k context
    assert "alpha/small" not in ids  # 6k free tokens/min
    assert result.skipped["context window too small"] == ["local/tiny"]
    assert result.skipped["request exceeds free tokens/min"] == ["alpha/small"]


def test_images_need_a_vision_model(registry):
    router = Router(registry, HealthTracker())
    assert ranked(router, chat_profile(has_images=True, needs=["vision"])) == ["beta/mid"]


def test_failed_models_cool_down_and_auth_errors_disable_the_provider(registry):
    now = [1000.0]
    health = HealthTracker(clock=lambda: now[0])
    router = Router(registry, health)

    health.record_failure(registry.get("beta/mid"), "rate_limit", retry_after=30)
    assert "beta/mid" not in ranked(router, chat_profile())
    now[0] += 31
    assert "beta/mid" in ranked(router, chat_profile())

    health.record_failure(registry.get("alpha/small"), "auth")
    result = router.rank(chat_profile())
    assert result.skipped["provider key rejected"] == ["alpha/strong", "alpha/small"]


def test_request_specific_errors_do_not_cool_down(registry):
    health = HealthTracker()
    health.record_failure(registry.get("beta/mid"), "context")
    health.record_failure(registry.get("beta/mid"), "bad_request")
    assert health.unavailable_reason(registry.get("beta/mid")) is None


def test_uninstalled_local_models_are_skipped(registry):
    registry.get("local/tiny").installed = False
    router = Router(registry, HealthTracker())
    assert router.rank(chat_profile()).skipped["not installed"] == ["local/tiny"]


def test_quality_uses_multilingual_skill_for_non_latin_input(registry):
    model = registry.get("beta/mid").model_copy(update={"skills": {"chat": 0.9, "translate": 0.3}})
    latin = predicted_quality(model, chat_profile())
    gujarati = predicted_quality(model, chat_profile(script="gujarati"))
    assert gujarati < latin


def test_reasoning_models_get_a_bonus_when_reasoning_is_needed(registry):
    model = registry.get("alpha/strong")
    plain = predicted_quality(model, chat_profile(task="math", complexity=0.3))
    needy = predicted_quality(model, chat_profile(task="math", complexity=0.3, needs=["reasoning"]))
    assert needy > plain


def test_scarcity_scale(registry):
    assert scarcity(registry.get("local/tiny")) == 0.0
    assert 0.6 < scarcity(registry.get("alpha/strong")) < 0.8  # 20/day
    assert scarcity(registry.get("beta/mid")) < 0.2  # 14,400/day


def test_real_registry_routes_hard_code_to_a_strong_model():
    from tempo.registry import Registry

    env = {"GROQ_API_KEY": "x", "CEREBRAS_API_KEY": "x", "OPENROUTER_API_KEY": "x"}
    router = Router(Registry.load(env=env), HealthTracker())
    profile = analyze(
        [
            {
                "role": "user",
                "content": "Write a production-ready concurrent rate limiter in Rust. It must "
                "handle edge cases and include tests.",
            }
        ]
    )
    top = router.rank(profile).candidates[0]
    assert top.model.strength >= 0.8
