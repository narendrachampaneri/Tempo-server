"""Free capacity, what is left today, the fallback to local models, and local first."""

from conftest import make_engine, user
from typer.testing import CliRunner

from tempo import budget


def test_quota_view_per_provider():
    engine, _ = make_engine()
    view = {q.provider: q for q in budget.quota_view(engine)}
    assert view["alpha"].per_day == 20 + 14400 and view["alpha"].left_today == 20 + 14400
    assert view["local"].local and view["local"].left_today is None
    engine.quota.record(engine.registry.get("alpha/strong"), "server")
    view = {q.provider: q for q in budget.quota_view(engine)}
    assert view["alpha"].left_today == 19 + 14400
    assert 0 < view["alpha"].resets_in_s <= 24 * 3600
    assert budget.resets_text(3 * 3600 + 5 * 60) == "3h 05m"


def use_up_hosted(engine):
    for model in engine.registry.all():
        if not engine.registry.providers[model.provider].local:
            model.free_rpd = 1
            engine.quota.record(model, "server")


async def test_when_every_free_quota_is_used_up_local_models_answer_and_say_so():
    engine, backend = make_engine()
    use_up_hosted(engine)
    assert budget.all_used_up(budget.quota_view(engine))
    result = await engine.complete(user("Explain why the sky is blue, step by step"))
    assert result.error is None and result.model == "local/tiny"
    notes = [e.text for e in result.events if e.type == "note"]
    assert notes and "Free quota is used up on every provider" in notes[0]
    assert "local/tiny" in notes[0]


async def test_used_up_with_no_local_model_explains_what_to_do():
    engine, _ = make_engine()
    engine.registry.providers["local"].base_env = "NOT_SET"  # Ollama not running
    use_up_hosted(engine)
    result = await engine.complete(user("hi"))
    assert result.error and "Free quota is used up on every provider" in result.error


async def test_local_first_sends_simple_questions_to_ollama():
    engine, backend = make_engine(local_first="auto")
    tiny = engine.registry.get("local/tiny")
    tiny.installed = None  # Ollama not reached: no local first
    assert (await engine.complete(user("hi there"))).model != "local/tiny"
    tiny.installed = True  # reported by a running Ollama
    simple = await engine.complete(user("hi there"))
    assert simple.model == "local/tiny"
    stage = next(e for e in simple.events if e.type == "stage_start")
    assert "local model saves free quota" in stage.text
    notes = [e.text for e in simple.events if e.type == "note"]
    assert notes and "Local first" in notes[0] and "TEMPO_LOCAL_FIRST=off" in notes[0]
    hard = await engine.complete(
        user("Write a production-ready, thread-safe LRU cache in Python with unit tests")
    )
    assert backend.called_for("draft")[-1] != "local/tiny" or hard.model != "local/tiny"
    best = await engine.complete(user("hi there"), engine.options(mode="best"))
    assert best.model != "local/tiny"


def test_cli_quota(tmp_path, monkeypatch):
    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    monkeypatch.setenv("TEMPO_ENABLE_MOCK", "1")
    result = CliRunner().invoke(app, ["quota"])
    assert result.exit_code == 0, result.output
    assert "Free requests left today" in result.output


def test_api_quota_panel_data():
    from fastapi.testclient import TestClient

    from tempo.api import create_app
    from tempo.config import Settings

    engine, _ = make_engine()
    client = TestClient(create_app(engine=engine, settings=Settings()))
    body = client.get("/api/quota").json()
    alpha = next(p for p in body["providers"] if p["provider"] == "alpha")
    assert alpha["left_today"] == alpha["per_day"] and alpha["resets_in"] != ""
    assert body["all_used_up"] is False
    use_up_hosted(engine)
    assert client.get("/api/quota").json()["all_used_up"] is True
