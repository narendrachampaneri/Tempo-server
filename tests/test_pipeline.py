"""The staged engine with scripted mock models: early stop, fixes, parallel stages, merging,
multi-part jobs, and the stage / time / free-quota budgets."""

import json

from conftest import ENV_ALL, judge_reply, make_engine, sleep, user

from tempo.providers import ProviderError

CODE_Q = "Write a Python function that reverses a string"  # cascade: draft, check, fix...


def events_of(result, kind):
    return [e for e in result.events if e.type == kind]


def jobs(result):
    return [e.data["job"] for e in events_of(result, "stage_start")]


async def test_stops_as_soon_as_the_answer_passes():
    engine, backend = make_engine(judge_score=9)
    result = await engine.complete(user(CODE_Q))
    assert jobs(result) == ["draft", "check"]
    assert result.stop_reason == "passed"
    assert backend.called_for("judge") and not backend.called_for("fix")
    check = events_of(result, "check")[0]
    assert check.data["passed"] and check.data["judge_model"]


async def test_failed_check_runs_a_fix_that_replaces_the_draft_live():
    scores = lambda cands: [9 if c.startswith("Fix") else 4 for c in cands]  # noqa: E731
    engine, backend = make_engine({"*:judge": judge_reply(scores)})
    result = await engine.complete(user(CODE_Q))
    assert jobs(result) == ["draft", "check", "fix", "check"]
    assert result.text.startswith("Fix from")
    assert result.score >= 0.7  # judge 9/10 minus heuristic soft issues (short, no code block)
    # The draft streamed first; the fix replaced it on screen.
    kinds = [e.type for e in result.events]
    first_delta = kinds.index("answer_delta")
    reset = kinds.index("answer_reset")
    assert first_delta < reset < len(kinds) - 1 - kinds[::-1].index("answer_delta")
    stages = [e.data["stage"] for e in events_of(result, "answer_delta")]
    assert stages[0] == 1 and stages[-1] == 3
    # The fixer is not the model whose draft failed.
    assert backend.called_for("fix")[0] != backend.called_for("draft")[0]


async def test_mixture_drafts_in_parallel_and_streams_one_of_them():
    slow = [sleep(0.15), ("answer", "Draft text.")]
    scripts = {"alpha/strong": slow, "beta/mid": slow, "local/tiny": slow}
    engine, backend = make_engine(scripts, judge_score=9)
    result = await engine.complete(user("hi"), engine.options(mode="best"))
    first = events_of(result, "stage_start")[0]
    assert first.data["job"] == "draft" and len(first.data["models"]) == 3
    assert len({m.split("/")[0] for m in first.data["models"]}) == 3  # three families
    assert backend.max_active >= 3  # the three drafts ran at the same time
    draft_end = events_of(result, "stage_end")[0]
    assert draft_end.data["ms"] < 400
    streamed_by = {e.data["model"] for e in events_of(result, "answer_delta")}
    assert len(streamed_by) == 1


async def test_mixture_that_fails_its_check_is_merged():
    scores = lambda cands: [9 if c.startswith("Merge") else 5 for c in cands]  # noqa: E731
    engine, backend = make_engine({"*:judge": judge_reply(scores)})
    result = await engine.complete(user("hi"), engine.options(strategy="mixture", mode="best"))
    assert jobs(result) == ["draft", "check", "merge", "check"]
    merge_prompt = backend.calls[[p for _, _, p in backend.calls].index("merge")][1]
    assert merge_prompt[-1]["content"].count("<candidate id=") == 3
    assert result.text.startswith("Merge from") and result.stop_reason == "passed"


async def test_last_stage_polishes_instead_of_starting_a_check_it_cannot_finish():
    engine, _ = make_engine(judge_score=3)
    result = await engine.complete(user(CODE_Q), engine.options(max_stages=3))
    assert jobs(result) == ["draft", "check", "polish"]
    assert result.stop_reason == "polished"
    final = events_of(result, "answer_final")[0]
    assert final.data["score"] is None and final.data["stage"] == 3


async def test_stage_budget_keeps_the_best_answer():
    engine, _ = make_engine(judge_score=3)
    result = await engine.complete(user(CODE_Q), engine.options(max_stages=2))
    assert jobs(result) == ["draft", "check"]
    assert result.stop_reason == "budget_stages"
    assert events_of(result, "budget")[0].data["reason"] == "stages"
    assert result.text.startswith("Answer from") and result.error is None


async def test_one_stage_streams_the_draft_without_checking_it():
    engine, backend = make_engine()
    result = await engine.complete(user(CODE_Q), engine.options(max_stages=1))
    assert jobs(result) == ["draft"]
    assert result.stop_reason == "unchecked" and len(backend.calls) == 1


async def test_many_stages_allowed_for_big_jobs():
    scores = lambda cands: [2 for _ in cands]  # noqa: E731
    engine, _ = make_engine({"*:judge": judge_reply(scores)})
    result = await engine.complete(user(CODE_Q), engine.options(max_stages=20, quota_budget=100))
    assert 5 < result.stages <= 20
    assert set(jobs(result)) >= {"draft", "check", "fix", "merge"}


async def test_time_budget_returns_the_best_answer_so_far():
    engine, _ = make_engine(
        {"*:fix": [sleep(5), ("answer", "too late")], "*:judge": judge_reply([4])}
    )
    result = await engine.complete(user(CODE_Q), engine.options(time_budget_s=1))
    assert result.stop_reason == "budget_time"
    assert events_of(result, "budget")[0].data["reason"] == "time"
    assert result.text.startswith("Answer from") and result.error is None


async def test_time_budget_with_no_answer_at_all_is_an_error():
    slow = [sleep(5), ("answer", "late")]
    engine, _ = make_engine(
        {m: slow for m in ("alpha/strong", "alpha/small", "beta/mid")},
        env={"ALPHA_KEY": "a", "BETA_KEY": "b"},
    )
    result = await engine.complete(user("hi"), engine.options(time_budget_s=1))
    assert result.error == "No answer was produced within the budget."


async def test_quota_budget_limits_parallel_drafts_and_skips_the_judge():
    env = {"ALPHA_KEY": "a", "BETA_KEY": "b"}  # no local model: every call costs quota
    engine, backend = make_engine(env=env, judge_score=10)
    result = await engine.complete(user("hi"), engine.options(mode="best", quota_budget=2))
    first = events_of(result, "stage_start")[0]
    assert len(first.data["models"]) == 2  # only two of three drafts affordable
    assert result.requests == 2 and not backend.called_for("judge")
    check = events_of(result, "check")[0]
    assert check.data["judge_model"] is None
    assert result.error is None


async def test_quota_budget_counts_fallback_attempts():
    env = {"ALPHA_KEY": "a", "BETA_KEY": "b"}
    failing = [ProviderError("unavailable", "down")]
    engine, backend = make_engine({"beta/mid": failing}, env=env)
    result = await engine.complete(user("hi"), engine.options(quota_budget=1))
    assert backend.called == ["beta/mid"]
    assert "free-quota budget" in result.error


async def test_local_models_do_not_use_the_quota_budget():
    engine, backend = make_engine(env={"LOCAL_BASE": "http://local"}, judge_score=9)
    result = await engine.complete(user(CODE_Q), engine.options(quota_budget=1))
    assert result.requests == 0 and result.error is None


async def test_stage_events_carry_what_the_thinking_window_needs():
    engine, _ = make_engine(judge_score=9)
    result = await engine.complete(user(CODE_Q))
    start = events_of(result, "stage_start")[0].data
    assert {
        "stage",
        "max_stages",
        "job",
        "models",
        "reason",
        "requests_left",
        "time_left_s",
    } <= set(start)
    end = events_of(result, "stage_end")[0].data
    assert {"stage", "job", "ms", "requests_left", "time_left_s", "quota"} <= set(end)
    model = start["models"][0]
    assert end["quota"][model]["rpd"] is not None  # quota left for the model used
    for event in result.events:
        if event.type in ("stage_start", "stage_end", "check", "plan", "done"):
            assert event.to_dict()["text"]


async def test_multi_part_request_is_split_answered_and_combined():
    question = (
        "Please help:\n1. Write a haiku about the monsoon season\n"
        "2. Explain how a rainbow forms in simple words\n"
        "3. Translate 'good morning' into Hindi please"
    )
    engine, backend = make_engine(judge_score=9)
    result = await engine.complete(user(question), engine.options(max_parallel=1, max_stages=20))
    assert jobs(result) == ["parts", "parts", "parts", "combine", "check"]
    combine_prompt = next(m for _, m, p in backend.calls if p == "combine")
    assert combine_prompt[-1]["content"].count("<candidate id=") == 3
    assert result.text.startswith("Combine from")


async def test_forced_decompose_asks_a_model_to_split():
    engine, backend = make_engine(judge_score=9)
    result = await engine.complete(
        user("Plan a trip and a budget"), engine.options(strategy="decompose")
    )
    assert jobs(result)[:3] == ["split", "parts", "combine"]
    assert backend.called_for("split")


async def test_unsplittable_request_falls_back_to_cascade():
    engine, _ = make_engine(
        {"*:split": [("answer", json.dumps({"parts": ["only one"]}))]}, judge_score=9
    )
    result = await engine.complete(user("Do the thing"), engine.options(strategy="decompose"))
    assert jobs(result) == ["split", "draft", "check"]


async def test_everything_is_logged_for_tuning():
    engine, _ = make_engine({"*:judge": judge_reply([4, 9])}, judge_score=9)
    result = await engine.complete(user(CODE_Q))
    store = engine.store
    row = store.question(result.question_id)
    assert row["final_answer"] == result.text and row["stages_used"] == result.stages
    assert json.loads(row["plan"])["strategy"] == "cascade"
    stages = store.query("SELECT job FROM stages WHERE question_id = ?", (result.question_id,))
    assert [s["job"] for s in stages] == jobs(result)
    scored = store.query(
        "SELECT model, score FROM calls WHERE question_id = ? AND score IS NOT NULL",
        (result.question_id,),
    )
    assert scored and all(0 <= s["score"] <= 1 for s in scored)


async def test_logging_can_be_turned_off():
    engine, _ = make_engine(log_questions=False)
    result = await engine.complete(user("hi"))
    assert engine.store.question(result.question_id) is None


def test_env_all_has_every_provider():
    assert set(ENV_ALL) == {"ALPHA_KEY", "BETA_KEY", "LOCAL_BASE"}


async def test_never_reports_a_pass_that_did_not_happen():
    # Every answer is in English for a Gujarati question: all fail the language check.
    english = [
        ("answer", "The capital of Gujarat is Gandhinagar, a planned city near the old town.")
    ]
    scripts = {f"*:{job}": english for job in ("draft", "fix", "merge", "polish")}
    engine, _ = make_engine(scripts, judge_score=9)
    result = await engine.complete(user("ગુજરાતની રાજધાની કઈ છે? ત્યાં જોવાલાયક સ્થળો જણાવો."))
    assert result.stop_reason == "not_passed"
    done = events_of(result, "done")[0]
    assert "passed its check" not in done.text or "no answer passed" in done.text
