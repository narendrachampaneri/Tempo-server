"""Measured speed (tempo/speed.py): the rolling record, routing by speed, planning stages that
fit the time budget, and never calling a model that can't finish in the time left. Fake
providers only: fast, slow and failing scripted models."""

import pytest
from conftest import judge_reply, make_engine, make_registry, sleep, user

from tempo.analyzer import analyze
from tempo.providers import ProviderError
from tempo.router import TOO_SLOW
from tempo.speed import WINDOW, SpeedBook, estimate_seconds
from tempo.store import Store


def events_of(result, kind):
    return [e for e in result.events if e.type == kind]


def test_record_moves_the_figures_toward_measured_calls():
    registry = make_registry()
    book = SpeedBook(registry)
    strong = registry.get("alpha/strong")  # prior: 2 s to the first token, 100 tokens/s
    for _ in range(8):
        book.record(strong, ttft_s=0.4, total_s=2.4, out_tokens=400)  # 200 tokens/s
    speed = book.speed(strong)
    assert speed.n == 8
    assert 0.4 < speed.ttft_s < 0.8  # mostly measured, a little prior
    assert 170 < speed.tps < 200
    assert strong.ttft_ms == round(speed.ttft_s * 1000)  # the router reads these


def test_the_record_is_rolling_and_ignores_one_odd_call():
    registry = make_registry()
    book = SpeedBook(registry)
    mid = registry.get("beta/mid")
    for _ in range(WINDOW):
        book.record(mid, ttft_s=5.0, total_s=10.0, out_tokens=500)  # an old slow spell
    for _ in range(WINDOW):
        book.record(mid, ttft_s=0.2, total_s=1.2, out_tokens=500)  # fast again
    book.record(mid, ttft_s=30.0, total_s=31.0, out_tokens=500)  # one odd call
    speed = book.speed(mid)
    assert speed.n == WINDOW  # only the last calls count
    assert speed.ttft_s < 0.5 and speed.tps > 400


def test_one_slow_call_is_enough_to_look_slow():
    """nemotron-3-super is fast on paper and took 43 s on the laptop: after that one call its
    estimate must say so, or the router keeps choosing it."""
    registry = make_registry()
    book = SpeedBook(registry)
    strong = registry.get("alpha/strong")  # prior: 2 s to the first token, 100 tokens/s
    before = estimate_seconds(strong, 500)
    book.record(strong, ttft_s=6.0, total_s=43.0, out_tokens=250)  # ~7 tokens/s
    after = estimate_seconds(strong, 500)
    assert before < 15 and after > 40


def test_short_replies_do_not_count_toward_tokens_per_second():
    registry = make_registry()
    book = SpeedBook(registry)
    mid = registry.get("beta/mid")
    book.record(mid, ttft_s=0.1, total_s=0.11, out_tokens=3)
    assert book.speed(mid).tps == pytest.approx(500)  # the prior, unchanged


def test_load_reads_recent_calls_from_the_log():
    registry = make_registry()
    store = Store()
    for _ in range(5):
        store.add_call(model="alpha/small", status="ok", ms=600, ttft_ms=100, output_tokens=250)
    store.add_call(model="alpha/small", status="error", ms=60000, ttft_ms=None, output_tokens=0)
    book = SpeedBook(registry)
    assert book.load(store) == 5
    assert book.speed(registry.get("alpha/small")).n == 5


def test_estimate_counts_the_answer_length_reasoning_and_continuations():
    registry = make_registry()
    mid = registry.get("beta/mid")  # 0.3 s + tokens / 500
    assert estimate_seconds(mid, 500) == pytest.approx(1.3)
    mid.max_output = 1000  # a 3,500-token page needs four pieces: four first tokens
    assert estimate_seconds(mid, 3500) == pytest.approx(0.3 * 4 + 7.0)
    strong = registry.get("alpha/strong")  # reasoning: 300 hidden tokens more
    assert estimate_seconds(strong, 100) == pytest.approx(2.0 + 400 / 100)


def test_expected_output_follows_what_was_asked_for():
    def tokens(q):
        return analyze([{"role": "user", "content": q}]).est_output_tokens

    page = tokens("Write a complete HTML page for a bakery with a menu and a contact form")
    assert page >= 3000
    assert tokens("What is the capital of France? Answer in one word.") <= 100
    assert 1000 < tokens("Write an 800 word essay on the monsoon") < 1300
    assert tokens("Write a Python function that reverses a string") == 700


def test_router_skips_models_that_cannot_finish_in_the_time_left():
    engine, _ = make_engine()
    profile = analyze(user("Explain how rainbows form"))
    route = engine.router.rank(profile, time_left_s=2.0)
    ids = [c.model.id for c in route.candidates]
    assert "beta/mid" in ids and "alpha/strong" not in ids
    assert "alpha/strong" in route.skipped[TOO_SLOW]


def test_auto_prefers_a_fast_model_for_a_simple_question_and_quality_for_a_hard_one():
    engine, _ = make_engine()
    strong, mid = engine.registry.get("alpha/strong"), engine.registry.get("beta/mid")
    mid.skills = {t: 0.8 for t in mid.skills}  # close to the strong model in quality
    strong.free_rpd = mid.free_rpd  # same free quota: only quality and speed differ
    simple = analyze(user("hi"))
    hard = analyze(
        user(
            "Prove step by step that there are infinitely many primes, then derive the prime "
            "number theorem's error term and explain each edge case rigorously"
        )
    )
    assert engine.router.rank(simple).candidates[0].model is mid
    assert engine.router.rank(hard).candidates[0].model is strong


async def test_a_model_measured_as_slow_is_not_called_when_it_cannot_finish():
    """Laptop test: nemotron-3-super took 43 s of a 60 s budget. Once measured, a model that
    slow isn't started with 30 s left; a faster one answers."""
    engine, backend = make_engine(judge_score=9)
    strong = engine.registry.get("alpha/strong")
    for _ in range(10):
        engine.speed.record(strong, ttft_s=3.0, total_s=43.0, out_tokens=700)
    question = user("Write a Python function that reverses a string")  # ~700 tokens
    estimate = estimate_seconds(strong, 700)
    assert estimate > 30  # measured: it can't finish in 30 s
    result = await engine.complete(question, engine.options(mode="best", time_budget_s=30))
    assert "alpha/strong" not in backend.called_for("draft")
    # A 200-token judge reply still fits in the time left, so it may judge.
    assert estimate_seconds(strong, 200) < 30
    assert result.error is None


async def test_with_nothing_fast_enough_and_no_answer_the_fastest_model_tries():
    engine, backend = make_engine(env={"ALPHA_KEY": "a"}, judge_score=9)
    for model in engine.registry.all():
        model.ttft_ms, model.tokens_per_sec = 20000, 10  # every model: over 20 s
    small = engine.registry.get("alpha/small")
    small.ttft_ms = 5000  # the fastest
    result = await engine.complete(user("hi"), engine.options(time_budget_s=2))
    assert backend.called_for("draft")[0] == "alpha/small"
    notes = [e.data["message"] for e in events_of(result, "note")]
    assert any("trying the fastest, alpha/small" in n for n in notes)
    assert result.error is None


async def test_calls_update_the_measured_speed():
    scripts = {"beta/mid": [sleep(0.05), ("answer", "word " * 100)]}
    engine, _ = make_engine(scripts, judge_score=9)
    await engine.complete(user("hi"))
    speed = engine.speed.speed(engine.registry.get("beta/mid"))
    assert speed.n >= 1 and speed.ttft_s < 0.3


async def test_the_plan_shows_its_estimates_and_fits_stages_to_the_budget():
    engine, _ = make_engine({"*:judge": judge_reply([3])})
    for model in engine.registry.all():
        model.ttft_ms, model.tokens_per_sec = 1000, 100  # ~8 s per 700-token answer
    result = await engine.complete(
        user("Write a Python function that reverses a string"),
        # draft 8 s + check 3 s fit; a fix (8 s with the quickest model) doesn't
        engine.options(max_stages=10, time_budget_s=15, quota_budget=50),
    )
    plan = events_of(result, "plan")[0]
    assert plan.data["estimates"]["draft"] == pytest.approx(8.0, abs=1.0)
    assert plan.data["fits"] < 10 and plan.data["max_stages"] == plan.data["fits"]
    assert "answer ~700 tokens, ~8s" in plan.text
    assert result.stop_reason == "budget_time"
    budget = events_of(result, "budget")[0]
    assert "leaves no time for another stage" in budget.data["detail"]


async def test_a_failing_fast_model_falls_back_and_is_still_timed():
    scripts = {"beta/mid": [ProviderError("unavailable", "down")]}
    engine, backend = make_engine(scripts, judge_score=9)
    result = await engine.complete(user("hi"))
    assert result.error is None and backend.called_for("draft")[0] == "beta/mid"
    assert engine.speed.speed(engine.registry.get("beta/mid")).n == 0  # failures aren't timed


async def test_best_mode_leaves_out_a_draft_that_would_crowd_out_the_check_and_merge():
    engine, backend = make_engine(judge_score=9)
    for model in engine.registry.all():
        model.ttft_ms, model.tokens_per_sec = 10_000, 100  # ~11 s an answer, 12 s a check
    engine.registry.get("alpha/strong").ttft_ms = 45_000  # ~49 s: fits 60 s, alone
    result = await engine.complete(user("hi"), engine.options(mode="best", time_budget_s=60))
    draft = events_of(result, "stage_start")[0]
    assert draft.data["job"] == "draft" and "alpha/strong" not in draft.data["models"]
    assert len(draft.data["models"]) == 3  # three families still draft
    notes = [e.data["message"] for e in events_of(result, "note")]
    assert any("left out alpha/strong" in n for n in notes)
    assert "alpha/strong" not in backend.called_for("draft")
