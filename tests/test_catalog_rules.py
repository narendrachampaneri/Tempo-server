"""Owner's step-2 rules: model types, previews, specialists, data policy, training verdicts,
and providers that are off by default or blocked for some jobs."""

import pytest

from tempo.analyzer import analyze
from tempo.health import HealthTracker
from tempo.registry import Registry
from tempo.router import Router
from tempo.types import ModelInfo, ProviderInfo


def profile(text: str):
    return analyze([{"role": "user", "content": text}])


def model(model_id: str, **extra) -> ModelInfo:
    provider = model_id.split("/")[0]
    values = {"strength": 0.6, "skills": {}, "ttft_ms": 300, "tokens_per_sec": 200}
    values.update(extra)
    return ModelInfo(id=model_id, provider=provider, name=model_id, **values)


def registry(*models: ModelInfo, **providers: dict) -> Registry:
    infos = {
        "cloud": ProviderInfo(id="cloud", label="Cloud", key_env="CLOUD_KEY"),
        "local": ProviderInfo(id="local", label="Local", base_env="LOCAL_BASE", local=True),
        "trial": ProviderInfo(
            id="trial",
            label="Trial",
            key_env="TRIAL_KEY",
            enabled=False,
            blocked_for=["eval", "collect"],
        ),
        "byok": ProviderInfo(id="byok", label="BYOK", key_env="BYOK_KEY", byok_only=True),
    }
    for pid, extra in providers.items():
        infos[pid] = infos[pid].model_copy(update=extra)
    env = {"CLOUD_KEY": "k", "LOCAL_BASE": "http://x", "TRIAL_KEY": "k", "BYOK_KEY": "k"}
    return Registry(infos, list(models), env=env)


def ranked(reg: Registry, text: str, **kwargs):
    return Router(reg, HealthTracker()).rank(profile(text), **kwargs)


def test_only_chat_capable_models_get_chat_requests():
    reg = registry(
        model("cloud/chatty"),
        model("cloud/whisper", type="speech-to-text"),
        model("cloud/guard", type="safety"),
        model("cloud/coder", type="code"),
        model("cloud/eyes", type="vision"),
    )
    route = ranked(reg, "hello there")
    assert {c.model.id for c in route.candidates} == {"cloud/chatty", "cloud/coder", "cloud/eyes"}
    assert "cloud/whisper" in route.skipped["not a chat model (speech-to-text)"]


def test_previews_rank_below_stable_models_and_the_router_fallback_comes_last():
    reg = registry(
        model("cloud/preview", preview=True, strength=0.95),
        model("cloud/stable", strength=0.3),
        model("cloud/router", fallback_only=True, strength=1.0, endpoints=0),
    )
    order = [c.model.id for c in ranked(reg, "hello there").candidates]
    assert order == ["cloud/stable", "cloud/preview", "cloud/router"]


def test_expired_models_and_models_without_endpoints_are_dropped():
    reg = registry(
        model("cloud/old", expires="2020-01-01"),
        model("cloud/gone", endpoints=0),
        model("cloud/ok"),
    )
    route = ranked(reg, "hello there")
    assert [c.model.id for c in route.candidates] == ["cloud/ok"]
    assert route.skipped["expired"] == ["cloud/old"]
    assert route.skipped["no endpoints serving it"] == ["cloud/gone"]


def test_degraded_models_rank_down():
    reg = registry(
        model("cloud/flaky", uptime_30m=80.0, strength=0.9),
        model("cloud/steady", uptime_30m=99.5, strength=0.9),
    )
    candidates = ranked(reg, "hello there").candidates
    assert candidates[0].model.id == "cloud/steady"
    assert candidates[1].utility < candidates[0].utility - 0.1


def test_specialists_answer_only_questions_in_their_field():
    reg = registry(model("cloud/general"), model("cloud/fin", domain="finance"))
    general = ranked(reg, "Write a poem about the sea")
    assert [c.model.id for c in general.candidates] == ["cloud/general"]
    finance = ranked(reg, "How do dividends and income tax work for an index fund?")
    assert {c.model.id for c in finance.candidates} == {"cloud/general", "cloud/fin"}
    assert profile("What are the side effects of this medication?").domain == "health"


def test_private_requests_skip_models_that_may_log_or_train():
    reg = registry(
        model("cloud/logs", data_policy="may-log"),
        model("cloud/trains", data_policy="may-train"),
        model("cloud/clean", data_policy="ok"),
        model("cloud/unknown"),
    )
    route = ranked(reg, "hello there", no_logging=True)
    assert {c.model.id for c in route.candidates} == {"cloud/clean", "cloud/unknown"}
    assert len(ranked(reg, "hello there").candidates) == 4


def test_training_verdict_comes_from_the_model_licence_for_local_models():
    reg = registry(
        model("local/qwen", licence="Apache-2.0"),
        model("local/phi", licence="MIT"),
        model("local/llama", licence="llama3.2"),
        model("local/unknown"),
        model("cloud/any"),
        model("cloud/stated", training_on_outputs="yes"),
    )
    verdicts = {m.id: reg.training_verdict(m) for m in reg.all()}
    assert verdicts == {
        "local/qwen": "yes",
        "local/phi": "yes",
        "local/llama": "unclear",
        "local/unknown": "unclear",
        "cloud/any": "unclear",
        "cloud/stated": "yes",
    }
    route = ranked(reg, "hello there", training_only=True)
    assert {c.model.id for c in route.candidates} == {"local/qwen", "local/phi", "cloud/stated"}


def test_disabled_and_byok_only_providers():
    reg = registry(model("trial/m"), model("byok/m"))
    assert not reg.is_configured("trial") and not reg.is_configured("byok")
    assert reg.blocked_for("trial", "eval") and reg.blocked_for("trial", "collect")
    enabled = Registry(
        reg.providers, reg.all(), env={"TRIAL_KEY": "k", "TEMPO_ENABLE_PROVIDERS": "trial"}
    )
    assert enabled.is_configured("trial")
    from tempo.types import Access

    mine = Access(user_id="u", user_keys={"byok": "users-own-key"})
    assert reg.is_configured("byok", mine)
    assert "api_key" not in reg.credentials("byok")  # the server's key is never used


def test_cerebras_is_off_by_default_and_never_used_for_eval_or_collect():
    reg = Registry.load(env={"CEREBRAS_API_KEY": "placeholder"})
    assert not reg.is_configured("cerebras")
    assert reg.blocked_for("cerebras", "eval") and reg.blocked_for("cerebras", "collect")
    assert "payment method" in (reg.providers["cerebras"].free_tier_note or "")


async def test_eval_and_collect_refuse_blocked_providers():
    from conftest import make_engine

    from tempo import collect as col
    from tempo.evals import run_evals

    engine, backend = make_engine()
    engine.registry.providers["alpha"].blocked_for = ["eval", "collect"]
    await run_evals(engine, engine.registry.all(), tasks=["chat"], limit=1)
    assert not any(m.startswith("alpha/") for m in backend.called)
    assert "alpha" not in col.default_providers(engine.registry)
    with pytest.raises(ValueError, match="not allowed for tempo-server collect: alpha"):
        await col.run(engine, {}, providers=["alpha"])


# --- Owner's step-3 decisions on the step-2 providers ------------------------------------------


def test_nvidia_is_owner_only_and_never_in_demo_mode():
    from tempo.types import Access

    env = {"NVIDIA_API_KEY": "placeholder", "TEMPO_ENABLE_PROVIDERS": "nvidia"}
    reg = Registry.load(env=env)
    assert reg.is_configured("nvidia", Access(user_id="local"))
    assert reg.is_configured("nvidia", Access(user_id="admin"))
    assert not reg.is_configured("nvidia", Access(user_id="some-other-user"))
    demo = Registry.load(env={**env, "TEMPO_ENABLE_MOCK": "1"})
    assert not demo.is_configured("nvidia", Access(user_id="local"))
    assert not Registry.load(env={"NVIDIA_API_KEY": "placeholder"}).is_configured("nvidia")


def test_cohere_answers_only_with_a_users_own_key_and_never_judges():
    from conftest import make_engine

    from tempo.types import Access

    reg = Registry.load(env={"COHERE_API_KEY": "server-key-never-used"})
    assert not reg.is_configured("cohere")
    assert reg.is_configured("cohere", Access(user_id="u", user_keys={"cohere": "own-trial"}))
    for job in ("eval", "collect", "judge", "export"):
        assert reg.blocked_for("cohere", job)

    engine, backend = make_engine()
    engine.registry.providers["beta"].blocked_for = ["judge"]
    import asyncio

    # A cascade: draft, then a judge from another family, which must not be beta.
    asyncio.run(engine.complete([{"role": "user", "content": "Explain why the sky is blue"}]))
    judges = backend.called_for("judge")
    assert judges and not any(m.startswith("beta/") for m in judges)


def test_mistral_text_outputs_are_yes_and_opting_out_needs_the_setting():
    reg = Registry.load(env={"MISTRAL_API_KEY": "placeholder"})
    small = model("mistral/mistral-small-latest")
    assert reg.training_verdict(small) == "yes"
    assert reg.data_policy(small) == "may-train"
    opted = Registry.load(env={"MISTRAL_API_KEY": "placeholder", "TEMPO_OPTED_OUT": "mistral"})
    assert opted.data_policy(small) == "may-log"  # no written zero-retention: still flagged
    from tempo.types import Access

    own = Access(user_id="u", user_keys={"mistral": "their-own"})
    assert opted.data_policy(small, own) == "may-train"  # the owner's opt-out isn't theirs


def test_cloudflare_open_licence_models_are_yes_others_follow_their_licence():
    reg = Registry.load(env={})
    assert reg.training_verdict(model("cloudflare/@cf/a", licence="Apache-2.0")) == "yes"
    assert reg.training_verdict(model("cloudflare/@cf/b", licence="MIT")) == "yes"
    assert reg.training_verdict(model("cloudflare/@cf/c", licence="llama")) == "unclear"
    assert reg.training_verdict(model("cloudflare/@cf/d")) == "unclear"
    from tempo.sync import licence_from_hint

    assert licence_from_hint("https://www.apache.org/licenses/LICENSE-2.0") == "Apache-2.0"
    assert licence_from_hint("https://opensource.org/licenses/MIT") == "MIT"
    assert licence_from_hint("https://llama.meta.com/llama3/license/") == "llama"


def test_openrouter_free_models_may_log_unless_lifted_with_a_source():
    reg = Registry.load(env={})
    assert reg.data_policy(model("openrouter/x/y:free")) == "may-log"
    for m in reg.all():
        if m.provider == "openrouter" and m.data_policy == "ok":
            assert m.data_policy_note, f"{m.id}: a lifted flag needs its written source"


def test_zen_is_off_and_out_of_eval_collect_and_exports():
    reg = Registry.load(env={"TEMPO_ENABLE_PROVIDERS": "opencode"})
    assert not reg.providers["opencode"].enabled
    for job in ("eval", "collect", "export"):
        assert reg.blocked_for("opencode", job)
    assert reg.training_verdict(model("opencode/big-pickle", training_on_outputs="yes")) == "no"


def test_nvidia_is_refused_whenever_demo_models_are_loaded():
    env = {"NVIDIA_API_KEY": "placeholder", "TEMPO_ENABLE_PROVIDERS": "nvidia"}
    assert not Registry.load(env=env, include_mock=True).is_configured("nvidia")
