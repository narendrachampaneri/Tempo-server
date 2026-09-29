"""Model-slot behaviour of the engine: fallback, streaming, errors, options.

Stage logic (checks, fixes, mixing, budgets) is covered in test_pipeline.py.
"""

from conftest import make_engine, user

from tempo.engine import collect
from tempo.providers import ProviderError


def types(result) -> list[str]:
    return [e.type for e in result.events if e.type not in ("answer_delta", "reasoning_delta")]


async def test_happy_path_drafts_checks_and_finishes():
    engine, backend = make_engine()
    result = await engine.complete(user("hi"))
    assert result.error is None
    assert result.model == "beta/mid"
    assert result.text == "Answer from beta/mid."
    assert types(result) == [
        "received",
        "analyze",
        "plan",
        "stage_start",
        "call_start",
        "call_end",
        "answer_ready",  # shown at once; the check runs in the background
        "stage_end",
        "stage_start",
        "check",
        "stage_end",
        "answer_final",
        "done",
    ]
    draft = next(e for e in result.events if e.type == "stage_start")
    assert draft.data["job"] == "draft" and draft.data["models"] == ["beta/mid"]
    assert draft.to_dict()["text"].startswith("Stage 1/3 · draft · beta/mid")
    # An easy chat question is checked with heuristics only: no judge call.
    assert backend.called == ["beta/mid"]
    assert result.stop_reason == "passed" and result.stages == 2


async def test_falls_back_when_a_model_fails_before_answering():
    engine, backend = make_engine({"beta/mid": [ProviderError("rate_limit", "429", retry_after=5)]})
    result = await engine.complete(user("hi"))
    assert result.error is None
    drafts = backend.called_for("draft")
    assert drafts[0] == "beta/mid"
    assert result.model == drafts[1] != "beta/mid"
    assert "call_error" in types(result) and "fallback" in types(result)
    # The failed model now cools down, so the next request routes around it.
    second = await engine.complete(user("hello again"))
    assert second.model != "beta/mid"


async def test_auth_error_skips_other_models_from_the_same_provider():
    engine, backend = make_engine(
        {
            "alpha/strong": [ProviderError("auth", "401")],
            "beta/mid": [ProviderError("unavailable", "503")],
        }
    )
    result = await engine.complete(user("hi"), engine.options(model="alpha/strong"))
    # alpha/small would be next after beta/mid, but alpha's key was rejected.
    assert backend.called_for("draft") == ["alpha/strong", "beta/mid", "local/tiny"]
    assert result.model == "local/tiny"


async def test_mid_stream_failure_restarts_answer_on_another_model():
    engine, _ = make_engine(
        {"beta/mid": [("answer", "Partial"), ProviderError("unavailable", "connection reset")]}
    )
    result = await engine.complete(user("hi"))
    assert "answer_reset" in types(result)
    assert "Partial" not in result.text
    assert result.model != "beta/mid"


async def test_mid_stream_failure_is_an_error_when_restart_is_not_allowed():
    engine, backend = make_engine(
        {"beta/mid": [("answer", "Partial"), ProviderError("unavailable", "connection reset")]}
    )
    result = await engine.complete(user("hi"), engine.options(restart_on_partial_failure=False))
    assert result.error and "failed mid-answer" in result.error
    assert backend.called == ["beta/mid"]


async def test_empty_answer_counts_as_failure():
    engine, backend = make_engine({"beta/mid": [("answer", "  \n")]})
    result = await engine.complete(user("hi"))
    call_error = next(e for e in result.events if e.type == "call_error")
    assert call_error.data["kind"] == "empty"
    assert len(backend.called_for("draft")) == 2 and result.error is None


async def test_reports_error_when_every_attempt_fails():
    failing = [ProviderError("unavailable", "down")]
    engine, backend = make_engine(
        {m: failing for m in ("alpha/strong", "alpha/small", "beta/mid", "local/tiny")},
        max_attempts=3,
    )
    result = await engine.complete(user("hi"))
    assert len(backend.called) == 3
    assert result.error == "Every model tried for this stage failed."
    assert set(backend.called) <= set(result.events[-1].data["tried"])


async def test_no_configured_provider_gives_setup_hint():
    engine, backend = make_engine(env={})
    result = await engine.complete(user("hi"))
    assert result.error == (
        "No model yet: run `tempo-server setup` to add a free key, or start Ollama."
    )
    assert backend.called == []


async def test_explicit_model_requests():
    engine, backend = make_engine()
    result = await engine.complete(user("hi"), engine.options(model="local/tiny"))
    assert result.model == "local/tiny"
    draft = next(e for e in result.events if e.type == "stage_start")
    assert draft.data["reason"] == "requested by caller"

    unknown = await engine.complete(user("hi"), engine.options(model="nope/model"))
    assert unknown.error == "Unknown model: nope/model"

    engine, backend = make_engine(env={"BETA_KEY": "b"})
    unavailable = await engine.complete(user("hi"), engine.options(model="alpha/strong"))
    assert unavailable.error == "alpha/strong is unavailable: provider not configured"
    assert backend.called == []


async def test_system_prompt_added_only_when_missing():
    engine, backend = make_engine()
    await engine.complete(user("hi"), engine.options(system_prompt="Be Tempo."))
    assert backend.calls[-1][1][0] == {"role": "system", "content": "Be Tempo."}

    own = [{"role": "system", "content": "Custom."}, *user("hi")]
    await engine.complete(own, engine.options(system_prompt="Be Tempo."))
    assert [m["content"] for m in backend.calls[-1][1] if m["role"] == "system"] == ["Custom."]


async def test_reasoning_is_collected_separately_from_the_answer():
    engine, _ = make_engine(
        {"beta/mid": [("reasoning", "thinking..."), ("answer", "Done."), ("answer", " Yes.")]}
    )
    result = await engine.complete(user("hi"))
    assert result.reasoning == "thinking..."
    assert result.text == "Done. Yes."
    assert [e.type for e in result.events].count("reasoning_delta") == 1


async def test_needs_a_user_message():
    engine, _ = make_engine()
    result = await collect(engine.run([{"role": "system", "content": "x"}]))
    assert result.error == "There is no user message to answer."


async def test_trace_excludes_stream_chunks_and_has_text():
    engine, _ = make_engine()
    result = await engine.complete(user("hi"))
    assert all(e["type"] not in ("answer_delta", "reasoning_delta") for e in result.trace)
    assert all("text" in e for e in result.trace if e["type"] != "answer_final")


async def test_options_are_clamped():
    engine, _ = make_engine()
    options = engine.options(max_stages=999, quota_budget=0, mode="weird", time_budget_s=0)
    assert options.max_stages == 50 and options.quota_budget == 1
    assert options.mode == "auto" and options.time_budget_s == 1.0
