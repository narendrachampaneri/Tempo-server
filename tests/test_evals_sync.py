"""Measured skill scores (probe set, live judge scores, latency) and registry sync/health."""

import httpx
import pytest
from conftest import make_engine, make_registry

from tempo.evals import SkillBook, grade, load_evalset, run_evals
from tempo.health import HealthTracker
from tempo.registry import Registry
from tempo.router import Router
from tempo.store import Store
from tempo.sync import RegistrySync, guess_model, verify_key
from tempo.types import QueryProfile


@pytest.mark.parametrize(
    ("answer", "rule", "expected"),
    [
        ("The answer is 397.8", {"number": 397.8}, 1.0),
        ("It's 1,250.00 INR", {"number": 1250}, 1.0),
        ("About 400", {"number": 397.8}, 0.0),
        ("Gracias!", {"contains": ["gracias"]}, 1.0),
        ("Thanks", {"contains": ["gracias"]}, 0.0),
        ("It is O(log n).", {"any": ["o(log n)", "o(logn)"]}, 1.0),
        ('```json\n{"name": "Arjun", "age": 34}\n```', {"json": {"name": "Arjun", "age": 34}}, 1.0),
        ('{"name": "Arjun", "age": 35}', {"json": {"name": "Arjun", "age": 34}}, 0.0),
        ("```python\ndef fizzbuzz(n):\n    return []\n```", {"python": "fizzbuzz"}, 1.0),
        ("```python\ndef fizzbuzz(n)\n    return []\n```", {"python": "fizzbuzz"}, 0.0),
        ("```python\nimport os\nos.system('rm -rf /')\n```", {"python": "fizzbuzz"}, 0.0),
    ],
)
def test_grading_rules(answer, rule, expected):
    assert grade(answer, rule) == expected


def test_evalset_covers_every_task_and_every_rule_is_known():
    items = load_evalset()
    assert {i.task for i in items} == {
        "chat",
        "code",
        "math",
        "reasoning",
        "writing",
        "summarize",
        "translate",
        "extract",
    }
    for item in items:
        assert grade("", item.grade) in (0.0, 1.0)


def profile(task="code"):
    return QueryProfile(
        task=task, complexity=0.2, script="latin", needs=[], input_tokens=20, est_output_tokens=200
    )


def test_skillbook_blends_prior_probe_and_live_scores():
    registry = make_registry()
    store = Store()
    book = SkillBook(registry, store)
    small = registry.get("alpha/small")
    assert book.skill(small, "code") == pytest.approx(0.5)  # prior only
    store.save_eval("alpha/small", "code", 5, 1.0)
    book.refresh(force=True)
    assert book.skill(small, "code") == pytest.approx((5 * 0.5 + 5 * 1.0) / 10)
    for _ in range(10):
        store.add_call(model="alpha/small", task="code", status="ok", score=0.0, ms=100)
    book.refresh(force=True)
    detail = book.detail(small, "code")
    assert detail["live_n"] == 10 and detail["eval_n"] == 5
    assert detail["skill"] == pytest.approx((2.5 + 5.0 + 0.5 * 10 * 0.0) / (5 + 5 + 5))


def test_live_latency_updates_speed_estimates():
    registry = make_registry()
    store = Store()
    book = SkillBook(registry, store)
    tiny = registry.get("local/tiny")
    for _ in range(20):
        store.add_call(model="local/tiny", status="ok", ms=1100, ttft_ms=100, output_tokens=100)
    book.refresh(force=True)
    assert tiny.ttft_ms < 800  # moved from the 800 ms prior toward the measured 100 ms
    assert tiny.tokens_per_sec > 40  # measured 100 tokens/s against a 40 prior


def test_router_follows_measured_skills():
    registry = make_registry()
    store = Store()
    book = SkillBook(registry, store)
    router = Router(registry, HealthTracker(), skill_of=book.skill)
    before = [c.model.id for c in router.rank(profile(), mode="best").candidates]
    store.save_eval("alpha/small", "code", 40, 1.0)
    store.save_eval("alpha/strong", "code", 40, 0.1)
    book.refresh(force=True)
    after = [c.model.id for c in router.rank(profile(), mode="best").candidates]
    assert after.index("alpha/small") < before.index("alpha/small")
    assert after.index("alpha/strong") > before.index("alpha/strong")


async def test_run_evals_grades_models_and_saves_scores():
    def answers(messages):
        question = messages[-1]["content"]
        if "17%" in question:
            return [("answer", "397.8")]
        return [("answer", "no idea")]

    engine, _ = make_engine({"*:eval": answers})
    models = [engine.registry.get("beta/mid")]
    seen = []
    results = await run_evals(engine, models, tasks=["math"], on_result=seen.append)
    assert len(results) == 1 and results[0].task == "math" and results[0].n == 5
    assert results[0].score == pytest.approx(0.2)
    assert seen == results
    saved = {(r["model"], r["task"]): r for r in engine.store.eval_results()}
    assert saved[("beta/mid", "math")]["score"] == pytest.approx(0.2)
    assert engine.skills.detail(models[0], "math")["eval_n"] == 5


async def test_run_evals_respects_quota_and_limit():
    engine, backend = make_engine({"*:eval": [("answer", "x")]})
    strong = engine.registry.get("alpha/strong")  # 20 free requests/day
    for _ in range(20):
        engine.quota.record(strong)
    results = await run_evals(engine, [strong], tasks=["chat"], limit=2)
    assert results[0].n == 0 and results[0].errors == 2
    assert backend.calls == []


# --- registry sync ------------------------------------------------------------------------


def provider_lists(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "openrouter.ai" in url:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "meta-llama/llama-3.3-70b-instruct:free",
                        "context_length": 131072,
                        "pricing": {"prompt": "0", "completion": "0"},
                    },
                    {
                        "id": "qwen/qwen3-235b-a22b:free",
                        "context_length": 40960,
                        "pricing": {"prompt": "0", "completion": "0"},
                    },
                    {
                        "id": "openai/gpt-5",
                        "pricing": {"prompt": "0.00001", "completion": "0.00003"},
                    },
                    {
                        "id": "acme/retired:free",
                        "pricing": {"prompt": "0", "completion": "0"},
                        "expiration_date": "2020-01-01",
                    },
                ]
            },
        )
    if "api.groq.com" in url:
        assert request.headers["authorization"] == "Bearer gk"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen/qwen3.8-27b", "context_window": 131072, "active": True},
                    {"id": "whisper-large-v3", "active": True},
                    {"id": "moonshotai/kimi-k2-instruct", "context_window": 131072, "active": True},
                ]
            },
        )
    if "generativelanguage" in url:
        assert request.headers["x-goog-api-key"] == "gm"
        assert "key" not in request.url.params  # never in the URL
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "inputTokenLimit": 1048576,
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )
    if "cerebras" in url:
        return httpx.Response(401, json={"error": "bad key"})
    return httpx.Response(404)


async def test_sync_adds_new_free_models_and_retires_missing_ones():
    env = {
        "GROQ_API_KEY": "gk",
        "GEMINI_API_KEY": "gm",
        "CEREBRAS_API_KEY": "ck",
        "TEMPO_ENABLE_PROVIDERS": "cerebras",
    }
    registry = Registry.load(env=env)
    health = HealthTracker()
    status = await RegistrySync(registry, health, httpx.MockTransport(provider_lists)).run()

    assert status["groq"].ok and status["groq"].listed == 3
    assert "groq/moonshotai/kimi-k2-instruct" in status["groq"].added
    # Registered by type; the router never sends it a chat request.
    assert registry.get("groq/whisper-large-v3").type == "speech-to-text"
    assert registry.get("groq/qwen/qwen3.8-27b").listed is True
    assert registry.get("groq/openai/gpt-oss-120b").listed is False  # no longer offered
    assert "groq/openai/gpt-oss-120b" in status["groq"].removed

    added = registry.get("openrouter/qwen/qwen3-235b-a22b:free")
    assert added is not None and added.source == "sync" and added.family == "qwen"
    assert registry.get("openrouter/openai/gpt-5") is None  # paid: not added
    assert registry.get("openrouter/acme/retired:free") is None  # past its expiration date
    assert registry.get("gemini/text-embedding-004").type == "embedding"

    assert not status["cerebras"].ok and status["cerebras"].error == "API key rejected"
    assert (
        health.unavailable_reason(registry.get("cerebras/gpt-oss-120b")) == "provider key rejected"
    )

    router = Router(registry, health)
    skipped = router.rank(profile("chat")).skipped
    assert "groq/openai/gpt-oss-120b" in skipped["no longer offered"]


async def test_sync_reports_outages_without_changing_the_registry():
    def down(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    registry = Registry.load(env={"GROQ_API_KEY": "gk"})
    status = await RegistrySync(registry, transport=httpx.MockTransport(down)).run()
    assert not status["groq"].ok and status["groq"].error.startswith("unreachable")
    assert registry.get("groq/qwen/qwen3.8-27b").listed is None


def test_guessed_priors_scale_with_size():
    small = guess_model("tiny-llama-1b", "groq", None, 4096)
    big = guess_model("llama-3.1-405b", "groq", None, 131072)
    moe = guess_model("qwen3-235b-a22b", "groq", None, 32768)
    assert small.strength < big.strength
    assert moe.tokens_per_sec > big.tokens_per_sec  # only 22B active parameters
    assert guess_model("qwen3-coder-30b", "groq", None, 1).skills["code"] > 0.6


def test_guesses_use_the_providers_own_fields():
    # The organisation's name ("thinkingmachines") must not make a model a reasoning model.
    assert not guess_model("thinkingmachines/inkling:free", "openrouter", None, 1).reasoning
    assert guess_model("vendor/plain:free", "openrouter", None, 1, reasoning=True).reasoning
    # No size in the id: take it from the Hugging Face id.
    hinted = guess_model(
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter",
        None,
        1,
        hint="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16",
    )
    assert hinted.tokens_per_sec == 1000  # 3B active parameters
    assert guess_model("acme/x:free", "openrouter", None, 1, hint="acme/Llama-8B").family == "llama"
    assert guess_model("poolside/laguna-s-2.1:free", "openrouter", None, 1).family == "poolside"
    assert guess_model("mystery-7b", "groq", None, 1).family == "unknown"


def test_expired_models_are_skipped():
    from tempo.registry import expired

    now = 1_790_000_000  # 2026-09-21 UTC
    assert expired("2026-09-01", now) and expired("2026-09-21", now)
    assert not expired("2026-10-09", now) and not expired(None, now) and not expired("soon", now)


async def test_a_400_that_says_the_key_is_bad_counts_as_rejected():
    def google(request):
        message = "API key not valid. Please pass a valid API key."
        body = {"error": {"code": 400, "message": message}}
        return httpx.Response(400, json=body)

    registry = Registry.load(env={"GEMINI_API_KEY": "bad"})
    health = HealthTracker()
    status = await RegistrySync(registry, health, httpx.MockTransport(google)).run()
    assert status["gemini"].error == "API key rejected"
    assert await verify_key(registry, "gemini", "bad", httpx.MockTransport(google)) is False


# :free models that existed with no endpoints on 2026-09-28 (owner's test case).
NO_ENDPOINTS = [
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "qwen/qwen3.6-plus:free",
    "z-ai/glm-4.5-air:free",
    "deepseek/deepseek-r1-0528:free",
]
FREE = {"prompt": "0", "completion": "0"}


def openrouter_live(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("/api/v1/models"):
            data = [{"id": m, "pricing": FREE, "context_length": 131072} for m in NO_ENDPOINTS]
            data += [
                {"id": "good/model:free", "pricing": FREE, "context_length": 65536},
                {"id": "flaky/model:free", "pricing": FREE},
                {"id": "down/model:free", "pricing": FREE},
                {"id": "nosuffix/model", "pricing": FREE},  # free without the :free suffix
                {"id": "openrouter/free", "pricing": FREE},
                {"id": "stealth/secret", "pricing": FREE},
                {"id": "vendor/model-fin:free", "pricing": FREE},
                {"id": "meta-llama/llama-guard-4-12b:free", "pricing": FREE},
                {"id": "paid/model", "pricing": {"prompt": "0.000001", "completion": "0"}},
            ]
            return httpx.Response(200, json={"data": data})
        if url.endswith("/endpoints"):
            model = url.split("/api/v1/models/", 1)[1].removesuffix("/endpoints")
            if model in NO_ENDPOINTS:
                endpoints = []
            elif model == "flaky/model:free":
                endpoints = [{"status": 0, "uptime_last_30m": 91.2, "pricing": FREE}]
            elif model == "down/model:free":
                endpoints = [{"status": -2, "uptime_last_30m": 99.0}]
            else:
                endpoints = [{"status": 0, "uptime_last_30m": 99.9, "max_completion_tokens": 8192}]
            return httpx.Response(200, json={"data": {"id": model, "endpoints": endpoints}})
        return httpx.Response(404)

    return handler


async def test_openrouter_free_models_are_checked_against_their_endpoints():
    calls: list[str] = []
    # The list and endpoints are public; the key (a placeholder) only lets the router route.
    registry = Registry.load(env={"OPENROUTER_API_KEY": "placeholder-key"})
    clock = [1_790_000_000.0]
    sync = RegistrySync(
        registry, transport=httpx.MockTransport(openrouter_live(calls)), clock=lambda: clock[0]
    )
    status = await sync.run()
    assert status["openrouter"].ok
    ids = {m.id for m in registry.all()}
    assert "openrouter/paid/model" not in ids
    assert registry.get("openrouter/nosuffix/model") is not None

    router = Router(registry, HealthTracker())
    route = router.rank(profile("chat"))
    usable = [c.model.id for c in route.candidates]
    for model in NO_ENDPOINTS:  # listed, but nothing serves them: never routed to
        assert registry.get(f"openrouter/{model}").endpoints == 0
        assert f"openrouter/{model}" in route.skipped["no endpoints serving it"]
    assert usable[-1] == "openrouter/openrouter/free"  # only as the very last fallback
    assert registry.get("openrouter/openrouter/free").fallback_only
    assert registry.get("openrouter/good/model:free").max_output == 8192
    assert registry.get("openrouter/flaky/model:free").degraded  # 91% < 95%
    assert registry.get("openrouter/down/model:free").degraded  # status below 0
    assert usable.index("openrouter/good/model:free") < usable.index("openrouter/flaky/model:free")
    secret = registry.get("openrouter/stealth/secret")
    assert secret.preview and secret.data_policy == "may-log"
    assert registry.get("openrouter/vendor/model-fin:free").domain == "finance"
    assert registry.get("openrouter/meta-llama/llama-guard-4-12b:free").type == "safety"

    # At most every 15 minutes: an immediate re-run reads no endpoints again.
    before = sum(u.endswith("/endpoints") for u in calls)
    await sync.run()
    assert sum(u.endswith("/endpoints") for u in calls) == before
    clock[0] += 15 * 60
    await sync.run()
    assert sum(u.endswith("/endpoints") for u in calls) > before


async def test_openrouter_key_reports_the_live_free_daily_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            assert request.headers["authorization"] == "Bearer placeholder-key"
            data = {"free_model_daily_requests": {"used": 3, "limit": 1000, "remaining": 997}}
            return httpx.Response(200, json={"data": data})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    registry = Registry.load(env={"OPENROUTER_API_KEY": "placeholder-key"})
    await RegistrySync(registry, transport=httpx.MockTransport(handler)).run()
    provider = registry.providers["openrouter"]
    assert provider.shared_rpd == 1000 and provider.limits_source.startswith("live:")
