import pytest

from tempo.config import Settings, parse_takeover
from tempo.store import Store


def test_question_lifecycle_and_feedback(tmp_path):
    store = Store(tmp_path / "t.db")
    store.start_question(
        "q1", user_id="u", mode="auto", messages=[{"role": "user", "content": "hi"}]
    )
    store.update_question("q1", final_answer="hello", stages_used=2, plan={"strategy": "cascade"})
    store.add_decision(
        "q1",
        stage=0,
        name="strategy",
        rules_value="cascade",
        laya_value="single",
        laya_probs={"single": 0.6},
        used="rules",
    )
    store.add_stage("q1", idx=1, job="draft", models=["m"], outputs=[{"text": "x"}])
    store.add_call(question_id="q1", stage=1, model="m", status="ok", ms=10, score=0.8, task="chat")
    assert store.set_feedback("q1", 1, "nice")
    assert not store.set_feedback("missing", -1)
    row = store.question("q1")
    assert row["final_answer"] == "hello" and row["feedback"] == 1
    assert '"cascade"' in row["plan"]
    assert store.live_scores()[0]["score"] == pytest.approx(0.8)
    # Reopening the same file keeps the data.
    assert Store(tmp_path / "t.db").question("q1")["stages_used"] == 2


def test_rejects_unknown_columns():
    store = Store()
    with pytest.raises(ValueError):
        store.update_question("q1", **{"feedback = 1; --": 1})
    with pytest.raises(ValueError):
        store.add_call(nope=1)


def test_quota_counters_accumulate():
    store = Store()
    store.quota_add("groq:k", "2026-09-28", 1, 100)
    store.quota_add("groq:k", "2026-09-28", 2, 50)
    assert store.quota_day("groq:k", "2026-09-28") == (3, 150)
    assert store.quota_day("groq:k", "2026-09-29") == (0, 0)


def test_settings_from_env(tmp_path):
    s = Settings.from_env(
        {
            "TEMPO_DATA_DIR": str(tmp_path),
            "TEMPO_MAX_STAGES": "25",
            "TEMPO_LAYA_TAKEOVER": "should_stop, task_type=auto",
        }
    )
    assert s.db_path == tmp_path / "tempo.db"
    assert s.max_stages == 25
    assert s.laya_mode("should_stop") == "laya"
    assert s.laya_mode("task_type") == "auto"
    assert s.laya_mode("strategy") == "shadow"
    assert Settings.from_env({"TEMPO_DATA_DIR": "memory"}).db_path is None
    assert Settings.from_env({"TEMPO_MAX_STAGES": "500"}).max_stages == 50


def test_takeover_parsing_errors():
    assert parse_takeover("all=auto")["next_model"] == "auto"
    with pytest.raises(ValueError):
        parse_takeover("nonsense")
    with pytest.raises(ValueError):
        parse_takeover("task_type=maybe")


def test_older_databases_get_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "tempo.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE laya_compare (decision TEXT PRIMARY KEY, n INTEGER, "
        "laya_accuracy REAL, rules_accuracy REAL, updated_at REAL)"
    )
    old.execute("INSERT INTO laya_compare VALUES ('quality', 60, 0.7, 0.6, 0)")
    old.commit()
    old.close()
    store = Store(path)
    assert store.laya_compare()["quality"]["laya_model"] is None
    store.save_laya_compare("quality", 80, 0.8, 0.6, laya_model="./tuned")
    assert store.laya_compare()["quality"]["laya_model"] == "./tuned"
