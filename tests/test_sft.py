"""tempo export-sft and tempo export-pairs: only "yes" rows, with licence and source."""

import json

from conftest import judge_reply, make_engine, user
from typer.testing import CliRunner

from tempo import sft
from tempo.store import Store

CODE_Q = "Write a Python function that reverses a string"


async def logged(tmp_path=None, **settings):
    """Questions of different kinds: a draft that fails then a fix that passes, a pass on the
    first draft, a 👎, and one from another user."""
    scores = lambda cands: [9 if c.startswith("Fix") else 4 for c in cands]  # noqa: E731
    engine, _ = make_engine(
        {"*:judge": judge_reply(scores)}, **({"data_dir": tmp_path} if tmp_path else {})
    )
    ids = {}
    for name, question in [
        ("fixed", CODE_Q),
        ("sky", "Explain why the sky is blue, step by step"),
        ("disliked", "Write a Python function that sums a list"),
    ]:
        result = await engine.complete(user(question))
        assert result.error is None
        ids[name] = result.question_id
    engine.store.set_feedback(ids["disliked"], -1)
    other = engine.access_for("someone-else")
    result = await engine.complete(user(CODE_Q), engine.options(access=other))
    ids["other_user"] = result.question_id
    return engine, ids


def all_yes(engine):
    for provider in engine.registry.providers.values():
        provider.training_on_outputs = "yes"


async def test_only_yes_rows_are_exported():
    engine, _ = await logged()
    rows, pairs, stats = sft.build(engine.store, engine.registry)
    # The test providers' terms are "unclear": nothing may become training data.
    assert rows == [] and pairs == [] and stats.skipped_terms > 0


async def test_sft_rows_are_checked_final_answers_with_source_and_licences():
    engine, ids = await logged()
    all_yes(engine)
    rows, pairs, stats = sft.build(engine.store, engine.registry, test_percent=0)
    by_q = {r["question_id"]: r for r in rows}
    assert ids["fixed"] in by_q
    row = by_q[ids["fixed"]]
    assert row["messages"][0] == {"role": "user", "content": CODE_Q}
    assert row["messages"][-1]["role"] == "assistant"
    assert row["messages"][-1]["content"].startswith("Fix from")  # the answer that passed
    assert row["source"]["dataset"] == "tempo-traffic" and row["source"]["license"]
    assert row["output_terms"] and all(t["verdict"] == "yes" for t in row["output_terms"].values())
    assert ids["disliked"] not in by_q  # 👎
    assert ids["other_user"] not in by_q and stats.skipped_user == 1  # needs consent
    assert stats.repetition["distinct_2"] > 0


async def test_pairs_prefer_the_draft_that_failed():
    engine, ids = await logged()
    all_yes(engine)
    _, pairs, _ = sft.build(engine.store, engine.registry, test_percent=0)
    pair = next(p for p in pairs if p["question_id"] == ids["fixed"])
    assert pair["prompt"] == [{"role": "user", "content": CODE_Q}]
    assert pair["chosen"][0]["content"].startswith("Fix from")
    assert pair["rejected"][0]["content"].startswith("Answer from")  # the failed draft
    assert pair["chosen_score"] > pair["rejected_score"]
    assert pair["rejected_issues"]
    assert pair["source"]["license"] and pair["output_terms"]


async def test_a_provider_whose_terms_check_failed_is_left_out():
    engine, _ = await logged()
    all_yes(engine)
    everything, _, _ = sft.build(engine.store, engine.registry)
    providers = {m.split("/")[0] for r in everything for m in r["output_terms"]}
    rows, _, stats = sft.build(engine.store, engine.registry, unverified=providers)
    assert everything and rows == [] and stats.skipped_terms


def test_repetition_measure():
    varied = sft.repetition(["the cat sat on the mat", "a dog ran in the park today"])
    loop = sft.repetition(["the cat sat on the mat the cat sat on the mat the cat sat on"])
    assert varied["distinct_2"] > loop["distinct_2"]
    assert loop["repeated_4"] > varied["repeated_4"] == 0.0


async def test_export_writes_train_test_and_readme(tmp_path):
    engine, _ = await logged()
    all_yes(engine)
    stats = sft.export(engine.store, engine.registry, tmp_path / "sft", "sft", test_percent=0)
    lines = (tmp_path / "sft" / "train.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == stats.rows > 0
    assert json.loads(lines[0])["messages"][-1]["role"] == "assistant"
    readme = (tmp_path / "sft" / "README.md").read_text(encoding="utf-8")
    assert "tempo-traffic" in readme and "distinct word pairs" in readme
    sft.export(engine.store, engine.registry, tmp_path / "pairs", "pairs", test_percent=0)
    pair = json.loads(
        (tmp_path / "pairs" / "train.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert set(pair) >= {"prompt", "chosen", "rejected", "source", "output_terms"}


def test_cli_exports(tmp_path, monkeypatch):
    import asyncio

    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    engine, _ = asyncio.run(logged(tmp_path / "data"))
    Store(tmp_path / "data" / "tempo.db")  # the log the CLI reads
    runner = CliRunner()
    for command, folder in (("export-sft", "s"), ("export-pairs", "p")):
        result = runner.invoke(app, [command, "--out", str(tmp_path / folder)])
        assert result.exit_code == 0, result.output
        assert (tmp_path / folder / "README.md").exists()
        # Test providers are not in the real registry, so their rows are left out.
        assert "Wrote 0" in result.output
