"""Models the provider no longer offers are skipped without a wasted attempt (step 10 laptop
test: a Gemini 2.5 model gave "model not found"). A fake provider list only; the suite runs
offline."""

import json

import httpx
from conftest import ENV_ALL, ScriptedBackend, make_registry, user

from tempo.config import Settings
from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.providers import ProviderError
from tempo.sync import CATALOG_FILE
from tempo.types import ModelInfo, ProviderInfo

LIST_URL = "https://api.groq.com/openai/v1/models"


def engine_with_groq(tmp_path=None, scripts=None, listed=("fast-one",)):
    registry = make_registry({**ENV_ALL, "GROQ_API_KEY": "gsk_placeholder"})
    registry.providers["groq"] = ProviderInfo(id="groq", label="Groq", key_env="GROQ_API_KEY")
    # the strongest model on paper, but Groq no longer offers it
    registry.add(
        ModelInfo(
            id="groq/retired-big",
            provider="groq",
            name="Retired",
            strength=0.99,
            skills={"chat": 0.99},
        )
    )
    registry.add(ModelInfo(id="groq/fast-one", provider="groq", name="Fast", strength=0.3))
    backend = ScriptedBackend(scripts or {}, judge_score=9)
    settings = Settings(data_dir=tmp_path, sync_interval_s=0) if tmp_path else Settings()
    engine = Engine(registry, lambda m: backend, settings=settings, health=HealthTracker())
    reads = []

    def groq(request: httpx.Request) -> httpx.Response:
        reads.append(str(request.url))
        assert request.headers["authorization"] == "Bearer gsk_placeholder"
        return httpx.Response(200, json={"data": [{"id": i} for i in listed]})

    engine._http_transport = httpx.MockTransport(groq)
    return engine, backend, reads


async def test_a_model_the_provider_no_longer_lists_is_never_tried():
    engine, backend, reads = engine_with_groq()
    result = await engine.complete(user("hi"), engine.options(mode="best"))
    assert result.error is None
    assert reads == [LIST_URL]  # the list was read before routing
    assert "groq/retired-big" not in backend.called
    notes = [e.data["message"] for e in result.events if e.type == "note"]
    assert "Groq's model list no longer has groq/retired-big: skipped." in notes
    # once per run: the next question doesn't read it again
    await engine.complete(user("hello"))
    assert reads == [LIST_URL]


async def test_lists_read_today_are_not_read_again_after_a_restart(tmp_path):
    engine, _, reads = engine_with_groq(tmp_path)
    await engine.startup(oneshot=True)
    await engine.complete(user("hi"))
    assert reads == [LIST_URL]
    saved = json.loads((tmp_path / CATALOG_FILE).read_text(encoding="utf-8"))
    retired = next(m for m in saved["models"] if m["id"] == "groq/retired-big")
    assert retired["listed"] is False

    again, backend, reads_again = engine_with_groq(tmp_path)
    await again.startup(oneshot=True)
    result = await again.complete(user("hi"), again.options(mode="best"))
    assert reads_again == []  # read less than a day ago (catalog.json)
    assert "groq/retired-big" not in backend.called and result.error is None


async def test_model_not_found_is_remembered_so_no_attempt_is_wasted_again(tmp_path):
    scripts = {"groq/retired-big": [ProviderError("not_found", "model not found")]}
    engine, backend, _ = engine_with_groq(tmp_path, scripts, listed=("fast-one", "retired-big"))
    await engine.startup(oneshot=True)
    first = await engine.complete(user("hi"), engine.options(mode="best"))
    assert first.error is None and backend.called.count("groq/retired-big") == 1
    notes = [e.data["message"] for e in first.events if e.type == "note"]
    assert any("not offered by its provider" in n for n in notes)
    await engine.complete(user("hello"), engine.options(mode="best"))
    assert backend.called.count("groq/retired-big") == 1  # not tried again
    saved = json.loads((tmp_path / CATALOG_FILE).read_text(encoding="utf-8"))
    assert next(m for m in saved["models"] if m["id"] == "groq/retired-big")["listed"] is False


async def test_an_unreadable_list_never_blocks_the_answer():
    engine, backend, _ = engine_with_groq()

    def down(request):
        raise httpx.ConnectError("no network", request=request)

    engine._http_transport = httpx.MockTransport(down)
    result = await engine.complete(user("hi"))
    assert result.error is None  # routing goes on with what is known
