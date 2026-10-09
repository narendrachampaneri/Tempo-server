"""Logs -> Laya fine-tuning dataset (typed-decisions format) and Laya-vs-rules comparison."""

import json

from conftest import judge_reply, make_engine, user
from test_laya import FakeLaya
from typer.testing import CliRunner

from tempo.config import Settings
from tempo.laya_decider import LayaDecider
from tempo.tuning import build_rows, compare, export

COLUMNS = {
    "id",
    "workflow",
    "split",
    "state",
    "questions",
    "gold",
    "factors",
    "label_agreement",
    "n_questions",
}


def notebook_targets(question, gold_q):
    """The target vector the official notebook builds (build_training_item, cell 6)."""
    t, crit = question["type"], question.get("criteria", {})
    if t == "choice":
        target = [gold_q["probabilities"].get(k, 0.0) for k in crit]
    elif t == "noul":
        target = [
            gold_q["probabilities"].get("false", 0.5),
            gold_q["probabilities"].get("true", 0.5),
        ]
    else:
        n_levels = len(crit) if isinstance(crit, list) else 4
        target = [gold_q["probabilities"].get(str(i), 0.0) for i in range(n_levels)]
    return target


async def logged_engine(tmp_path=None):
    """An engine with Laya in shadow mode and a few finished questions of different kinds."""
    scores = lambda cands: [9 if c.startswith(("Fix", "Merge")) else 4 for c in cands]  # noqa: E731
    settings = {"data_dir": tmp_path} if tmp_path else {}
    engine, _ = make_engine({"*:judge": judge_reply(scores)}, **settings)
    engine.laya = LayaDecider(
        Settings(), engine.store, loader=lambda: FakeLaya(picks={"should_stop": "B", "quality": 2})
    )
    engine.laya._load()
    ids = []
    for question, options in [
        ("Write a Python function that reverses a string", {}),  # draft fails, fix passes
        ("Explain why the sky is blue, step by step", {}),
        ("hi", {"mode": "best", "strategy": "mixture"}),  # several judged drafts, then merge
        ("Write a Python function that sums a list", {}),
    ]:
        result = await engine.complete(user(question), engine.options(**options))
        assert result.error is None
        ids.append(result.question_id)
    # Shadow predictions run in the background and reach the log when they finish; a slow CI
    # runner (Windows, 2026-10-09) had none of the should_stop ones logged yet.
    await engine.laya.drain()
    engine.store.set_feedback(ids[1], -1)
    return engine, ids


async def test_rows_match_the_typed_decisions_format():
    engine, _ = await logged_engine()
    rows, stats = build_rows(engine.store, engine.registry, test_percent=0, include_unclear=True)
    assert rows and stats.rows == len(rows) and stats.questions == 4
    assert {r["workflow"] for r in rows} == {"tempo_plan", "tempo_assess", "tempo_pick"}
    for row in rows:
        assert set(row) == COLUMNS and row["split"] == "train"
        state, questions, gold = (json.loads(row[k]) for k in ("state", "questions", "gold"))
        assert "request" in state and set(gold) == set(questions)
        assert row["n_questions"] == len(gold)
        for qid, q in questions.items():
            target = notebook_targets(q, gold[qid])
            assert sum(target) > 0.99  # a real distribution, not all zeros
            assert max(target) == target[int(gold[qid]["label"])] if q["type"] == "score" else True
            if q["type"] == "choice":
                assert gold[qid]["label"] in q["criteria"]
                assert len(q["criteria"]) <= 10


async def test_labels_come_from_outcomes():
    engine, ids = await logged_engine()
    rows, _ = build_rows(engine.store, engine.registry, test_percent=0, include_unclear=True)
    by_id = {r["id"]: r for r in rows}

    code_plan = json.loads(by_id[f"{ids[0]}_plan_0"]["gold"])
    assert code_plan["strategy"]["label"] == "cascade"  # it took one fix to pass
    assert code_plan["difficulty"]["label"] == "2"
    assert json.loads(by_id[f"{ids[0]}_assess_2"]["gold"])["should_stop"]["label"] == "B"
    last_assess = max(
        (r for r in rows if r["id"].startswith(f"{ids[0]}_assess")),
        key=lambda r: json.loads(r["factors"])["stage"],
    )
    assert json.loads(last_assess["gold"])["should_stop"]["label"] == "A"

    mixture_pick = next(r for r in rows if r["id"].startswith(f"{ids[2]}_pick_1"))
    sources = json.loads(mixture_pick["factors"])["decisions"]["next_model"]["gold_source"]
    assert sources == "compared"  # several drafts were judged on the same stage

    # Thumbs down on the final answer pulls its quality label down.
    disliked = max(
        (r for r in rows if r["id"].startswith(f"{ids[1]}_assess")),
        key=lambda r: json.loads(r["factors"])["stage"],
    )
    gold = json.loads(disliked["gold"])
    assert int(gold["quality"]["label"]) <= 1 and gold["should_stop"]["label"] == "B"


async def test_terms_of_use_filter():
    engine, _ = await logged_engine()
    # Test providers have no recorded terms (unclear). Their answers and judge grades shaped
    # every label, plan rows included, so nothing is exported by default.
    rows, stats = build_rows(engine.store, engine.registry)
    assert rows == [] and stats.unclear_terms_providers and stats.skipped_terms > 0
    unclear_rows, _ = build_rows(engine.store, engine.registry, include_unclear=True)
    assert {r["workflow"] for r in unclear_rows} == {"tempo_plan", "tempo_assess", "tempo_pick"}

    engine.registry.providers["beta"].training_on_outputs = "no"
    rows, _ = build_rows(engine.store, engine.registry, include_unclear=True)
    for row in rows:  # "no" is never used, whatever the flag
        assert not any(m.startswith("beta/") for m in json.loads(row["factors"])["output_models"])
    for provider in engine.registry.providers.values():
        provider.training_on_outputs = "yes"
    rows, stats = build_rows(engine.store, engine.registry)
    assert len(rows) == len(unclear_rows) and stats.skipped_terms == 0


def test_terms_accept_yaml_booleans_and_old_spellings():
    from tempo.types import ProviderInfo

    spelled = {True: "yes", False: "no", "allowed": "yes", "disallowed": "no", "unknown": "unclear"}
    for given, expected in spelled.items():
        assert ProviderInfo(id="p", label="P", training_on_outputs=given).training_on_outputs == (
            expected
        )


def test_registry_records_where_each_verdict_comes_from():
    from tempo.registry import Registry

    for provider in Registry.load().providers.values():
        assert provider.training_on_outputs in ("yes", "no", "unclear")
        if provider.local:
            continue
        assert provider.training_terms_url.startswith("https://")
        assert provider.training_terms_quote and provider.training_terms_checked


async def test_export_writes_files_the_notebook_can_load(tmp_path):
    engine, _ = await logged_engine()
    stats = export(engine.store, engine.registry, tmp_path / "ds", test_percent=50)
    train = (tmp_path / "ds" / "train.jsonl").read_text(encoding="utf-8").splitlines()
    test = (tmp_path / "ds" / "test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(train) + len(test) == stats.rows
    assert all(json.loads(line)["split"] == "test" for line in test)
    readme = (tmp_path / "ds" / "README.md").read_text(encoding="utf-8")
    assert 'load_dataset("json"' in readme and "laya_finetune_typed_decisions" in readme


async def test_compare_scores_laya_and_rules_on_held_out_rows():
    engine, _ = await logged_engine()
    results = {
        r.decision: r
        for r in compare(
            engine.store, engine.registry, test_percent=100, laya_model=engine.laya.checkpoint
        )
    }
    assert results["should_stop"].n > 0 and results["quality"].n > 0
    for r in results.values():
        assert 0.0 <= r.laya_accuracy <= 1.0 and 0.0 <= r.rules_accuracy <= 1.0
    saved = engine.store.laya_compare()
    assert saved["should_stop"]["n"] == results["should_stop"].n
    assert saved["should_stop"]["laya_model"] == engine.laya.checkpoint == "english/torch"
    rows, _ = build_rows(engine.store, engine.registry, test_percent=100, check_terms=False)
    decisions = [d for r in rows for d in json.loads(r["factors"])["decisions"].values()]
    assert {d["laya_model"] for d in decisions} == {"english/torch"}

    # Predictions logged by one checkpoint and runtime say nothing about another.
    tuned = compare(engine.store, engine.registry, test_percent=100, laya_model="./tuned/torch")
    assert all(r.n == 0 for r in tuned)


async def test_cli_export_and_compare(tmp_path, monkeypatch):
    from tempo.cli import app

    await logged_engine(tmp_path)
    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    runner = CliRunner()
    exported = runner.invoke(app, ["export-laya", "--out", str(tmp_path / "out")])
    assert exported.exit_code == 0, exported.output
    assert (tmp_path / "out" / "train.jsonl").exists()
    compared = runner.invoke(app, ["laya", "compare", "--test-percent", "100"])
    assert compared.exit_code == 0 and "should_stop" in compared.stdout
    status = runner.invoke(app, ["laya", "status"])
    assert status.exit_code == 0 and "next_model" in status.stdout


async def test_providers_whose_terms_check_failed_are_left_out_of_the_export():
    engine, _ = await logged_engine()
    for provider in engine.registry.providers.values():
        provider.training_on_outputs = "yes"
    all_rows, _ = build_rows(engine.store, engine.registry)
    rows, _ = build_rows(engine.store, engine.registry, unverified={"beta"})
    assert len(rows) < len(all_rows)
    for row in rows:
        assert not any(m.startswith("beta/") for m in json.loads(row["factors"])["output_models"])
