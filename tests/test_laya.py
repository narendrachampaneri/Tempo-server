"""Laya as the decision-maker: shadow mode, takeover, fallbacks (missing, error, timeout)."""

import asyncio
import json
import time

from conftest import ScriptedBackend, judge_reply, make_engine, user

from tempo.config import Settings, parse_takeover
from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.laya_decider import (
    MAX_SHORTLIST,
    REQUEST_CHARS,
    LayaDecider,
    checkpoint_name,
    default_loader,
)
from tempo.registry import Registry


class FakeLaya:
    """Stands in for laya.Router: answers every question, with overrides per question id."""

    def __init__(self, picks=None, delay=0.0, error=None, confidence=0.9):
        self.picks = picks or {}
        self.delay = delay
        self.error = error
        self.confidence = confidence
        self.calls = []

    def predict(self, state, questions):
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        self.calls.append((state, questions))
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "choice":
                keys = list(q["criteria"])
                choice = self.picks.get(qid, keys[0])
                probs = {k: (self.confidence if k == choice else 0.01) for k in keys}
                answers[qid] = {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probs,
                    "answer_confidence": self.confidence,
                }
            else:
                n = len(q["criteria"])
                level = self.picks.get(qid, 0)
                probs = {str(i): (self.confidence if i == level else 0.01) for i in range(n)}
                answers[qid] = {
                    "type": "score",
                    "score": float(level),
                    "probabilities": probs,
                    "answer_confidence": self.confidence,
                }
        return {"answers": answers, "routing": {"model": "english"}}


def with_laya(fake=None, takeover="", loader=None, timeout_ms=200.0, scripts=None, **kw):
    engine, backend = make_engine(scripts, **kw)
    settings = Settings(laya_takeover=parse_takeover(takeover), laya_timeout_ms=timeout_ms)
    engine.laya = LayaDecider(settings, engine.store, loader=loader or (lambda: fake))
    engine.laya._load()
    return engine, backend


def decisions(engine, question_id):
    return {
        (row["name"], row["stage"]): row
        for row in engine.store.query(
            "SELECT * FROM decisions WHERE question_id = ? ORDER BY id", (question_id,)
        )
    }


def events_of(result, kind):
    return [e for e in result.events if e.type == kind]


async def test_shadow_mode_logs_laya_but_rules_decide():
    fake = FakeLaya(picks={"strategy": "mixture", "task_type": "math"}, delay=0.05)
    engine, _ = with_laya(fake)
    started = time.perf_counter()
    result = await engine.complete(user("hi"))
    assert time.perf_counter() - started < 0.15  # nobody waits for a shadow prediction
    plan = events_of(result, "plan")[0]
    assert plan.data["strategy"] == "single"  # the rules' choice
    assert "Laya ready" in plan.to_dict()["text"]
    assert not events_of(result, "decision")  # shadow predictions add no lines
    await engine.laya.drain()
    rows = decisions(engine, result.question_id)
    row = rows[("strategy", 0)]
    assert json.loads(row["laya_value"]) == "mixture" and json.loads(row["rules_value"]) == "single"
    assert row["used"] == "rules" and row["laya_status"] == "ok" and row["laya_ms"] is not None
    assert json.loads(row["laya_state"])["request"] == "hi"
    assert json.loads(row["laya_question"])["type"] == "choice"
    assert ("should_stop", 2) in rows and ("quality", 2) in rows
    # One forward pass answers the whole plan group.
    assert len([c for c in fake.calls if "strategy" in c[1]]) == 1


async def test_takeover_lets_laya_decide():
    fake = FakeLaya(picks={"strategy": "mixture"})
    engine, _ = with_laya(fake, takeover="strategy")
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "mixture"
    assert decisions(engine, result.question_id)[("strategy", 0)]["used"] == "laya"


async def test_low_confidence_falls_back_to_rules():
    fake = FakeLaya(picks={"strategy": "mixture"}, confidence=0.3)
    engine, _ = with_laya(fake, takeover="strategy")
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "single"


async def test_slow_laya_times_out_rules_decide_and_the_prediction_is_logged_late():
    fake = FakeLaya(picks={"strategy": "mixture"}, delay=0.3)
    engine, _ = with_laya(fake, takeover="all", timeout_ms=50)
    started = time.perf_counter()
    result = await engine.complete(user("hi"))
    assert time.perf_counter() - started < 0.3  # never waits for the slow prediction
    assert result.error is None
    statuses = {e.data["laya_status"] for e in events_of(result, "decision")}
    assert statuses == {"timeout"}
    assert events_of(result, "plan")[0].data["strategy"] == "single"
    assert "Laya timeout" in events_of(result, "decision")[0].to_dict()["text"]
    # The prediction keeps running; when it finishes, the shadow log gets its answer.
    await asyncio.sleep(1.2)
    row = decisions(engine, result.question_id)[("strategy", 0)]
    assert row["laya_status"] == "late" and json.loads(row["laya_value"]) == "mixture"
    assert row["used"] == "rules" and row["laya_ms"] >= 250


async def test_too_many_pending_predictions_are_refused():
    fake = FakeLaya(delay=0.2)
    engine, _ = with_laya(fake, takeover="all", timeout_ms=10)
    results = await asyncio.gather(*(engine.complete(user(f"hi {i}")) for i in range(4)))
    statuses = {e.data["laya_status"] for r in results for e in events_of(r, "decision")}
    assert "busy" in statuses and "timeout" in statuses


async def test_waited_calls_ask_only_the_taken_over_questions():
    fake = FakeLaya(picks={"strategy": "mixture"}, delay=0.05)
    # Only strategy is taken over: it is asked on its own and waited for, and the rest of the
    # plan group runs in the background.
    engine, _ = with_laya(fake, takeover="strategy", timeout_ms=2000)
    await asyncio.gather(*(engine.complete(user(f"q {i}")) for i in range(3)))
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "mixture"
    assert [c for c in fake.calls if set(c[1]) == {"strategy"}]  # one question, not four
    await engine.laya.drain()
    rows = decisions(engine, result.question_id)
    assert rows[("strategy", 0)]["used"] == "laya"
    assert rows[("difficulty", 0)]["laya_status"] == "ok"  # logged from the background call


async def test_laya_errors_fall_back_to_rules():
    engine, _ = with_laya(FakeLaya(error=RuntimeError("CUDA out of memory")), takeover="all")
    result = await engine.complete(user("hi"))
    assert result.error is None
    assert {e.data["laya_status"] for e in events_of(result, "decision")} == {"error"}
    assert events_of(result, "plan")[0].data["strategy"] == "single"


async def test_missing_laya_is_logged_and_rules_decide():
    def loader():
        raise ImportError("No module named 'laya'")

    engine, _ = with_laya(loader=loader, takeover="all")
    assert engine.laya.status == "missing"
    result = await engine.complete(user("hi"))
    assert result.error is None
    assert "Laya not installed" in events_of(result, "plan")[0].to_dict()["text"]
    assert not events_of(result, "decision")  # no noise in the thinking window...
    rows = decisions(engine, result.question_id)
    assert rows[("strategy", 0)]["laya_status"] == "missing"  # ...but still logged for tuning
    assert json.loads(rows[("strategy", 0)]["laya_state"])["request"] == "hi"


async def test_laya_off_does_not_load():
    decider = LayaDecider(Settings(laya="off"), loader=lambda: FakeLaya())
    assert decider.start() is None and decider.status == "off"


def test_default_loader_runs_the_chosen_checkpoint_on_cpu(monkeypatch, tmp_path):
    from tempo import laya_runtime

    seen = {}

    def fake_load(**kwargs):
        seen.update(kwargs)
        return FakeLaya()

    monkeypatch.setattr(laya_runtime, "load", fake_load)
    settings = Settings.from_env(
        {
            "TEMPO_DATA_DIR": str(tmp_path),
            "TEMPO_LAYA_MODEL": " ./laya-tuned ",
            "TEMPO_LAYA_THREADS": "2",
        }
    )
    default_loader(settings)()
    assert seen == {
        "backend": "torch",
        "stock": "english",
        "model": "./laya-tuned",
        "threads": 2,
        "cache_dir": tmp_path / "laya",
        "device": None,
    }
    # Predictions are logged under the model *and* runtime that made them.
    assert checkpoint_name(settings) == "./laya-tuned/torch"
    assert checkpoint_name(Settings(laya_backend="onnx-int8")) == "english/onnx-int8"


def test_time_limit_is_measured_on_this_machine():
    decider = LayaDecider(Settings(), loader=lambda: FakeLaya(delay=0.12))
    decider._load()
    assert decider.status == "ready"
    assert set(decider.calibration) == {"plan", "assess", "pick"}
    slowest = max(decider.calibration.values())
    assert slowest >= 120 and decider.timeout_ms >= 1.5 * slowest - 10
    assert decider.timeout_ms == round(decider.timeout_ms, -1)  # whole tens of ms
    assert f"{decider.timeout_ms:.0f} ms limit" in decider.status_text()

    fixed = LayaDecider(Settings(laya_timeout_ms=350), loader=lambda: FakeLaya(delay=0.12))
    fixed._load()
    assert fixed.timeout_ms == 350 and fixed.calibration == {}  # a set limit is not measured

    fast = LayaDecider(Settings(), loader=lambda: FakeLaya())
    fast._load()
    assert fast.timeout_ms == 100  # never below 100 ms


async def test_laya_never_stops_on_a_hard_failure():
    refusal = [("answer", "I'm sorry, but I can't help with that.")]
    fake = FakeLaya(picks={"should_stop": "A", "quality": 4})
    engine, backend = with_laya(
        fake,
        takeover="should_stop,quality",
        scripts={"beta/mid": refusal, "*:judge": judge_reply([9])},
    )
    result = await engine.complete(user("hi"))
    # It kept going (a fix, or a polish on the last stage) despite Laya saying stop.
    assert backend.called_for("fix") or backend.called_for("polish")
    assert not result.text.startswith("I'm sorry")


async def test_laya_can_take_over_grading_and_stopping():
    fake = FakeLaya(picks={"should_stop": "B", "quality": 1})
    engine, backend = with_laya(fake, takeover="should_stop,quality", judge_score=10)
    result = await engine.complete(user("Write a Python function that reverses a string"))
    assert backend.called_for("fix")  # the judge liked the draft, Laya did not
    assert result.stages > 2


async def test_laya_picks_a_model_from_a_short_list():
    fake = FakeLaya(picks={"next_model": "B"})
    engine, backend = with_laya(fake, takeover="next_model")
    result = await engine.complete(user("hi"))
    rows = decisions(engine, result.question_id)
    pick = rows[("next_model", 1)]
    mapping = json.loads(pick["context"])["mapping"]
    assert backend.called_for("draft")[0] == mapping["B"]
    assert pick["used"] == "laya"


async def test_shortlist_is_capped_and_state_is_trimmed():
    env = {
        k: "x" for k in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    }
    registry = Registry.load(env=env, include_mock=True)
    backend = ScriptedBackend(judge_score=9)
    engine = Engine(registry, lambda m: backend, health=HealthTracker())
    fake = FakeLaya()
    engine.laya = LayaDecider(Settings(), engine.store, loader=lambda: fake)
    engine.laya._load()
    result = await engine.complete(user("Explain entropy " + "in great detail " * 200))
    pick_questions = [q for _, q in fake.calls if "next_model" in q]
    assert pick_questions
    assert all(len(q["next_model"]["criteria"]) <= MAX_SHORTLIST for q in pick_questions)
    assert all(len(state["request"]) <= REQUEST_CHARS for state, _ in fake.calls)
    assessed = [state for state, q in fake.calls if "should_stop" in q]
    assert assessed and all(len(state["answer"]) <= 700 for state in assessed)
    assert result.error is None


async def test_auto_takeover_follows_held_out_comparison():
    fake = FakeLaya(picks={"strategy": "mixture"})
    engine, _ = with_laya(fake, takeover="strategy=auto")
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "single"  # no comparison yet

    engine.store.save_laya_compare(
        "strategy", 120, laya_accuracy=0.81, rules_accuracy=0.64, laya_model=engine.laya.checkpoint
    )
    engine.laya._compare_loaded = 0
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "mixture"

    engine.store.save_laya_compare(
        "strategy", 120, laya_accuracy=0.5, rules_accuracy=0.64, laya_model=engine.laya.checkpoint
    )
    engine.laya._compare_loaded = 0
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "single"

    # A win measured on another checkpoint's predictions does not count for this one.
    engine.store.save_laya_compare(
        "strategy", 120, laya_accuracy=0.81, rules_accuracy=0.64, laya_model="./old/onnx-int8"
    )
    engine.laya._compare_loaded = 0
    result = await engine.complete(user("hi"))
    assert events_of(result, "plan")[0].data["strategy"] == "single"
