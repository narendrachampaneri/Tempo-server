"""`tempo-server train prepare`, the training kit and the Kaggle notebooks (no GPU, no training
libraries: the dry run in CI covers the training itself)."""

import ast
import json
import zipfile
from pathlib import Path

from test_sft import all_yes, logged

from tempo import notebooks, training, trainkit

ROOT = Path(__file__).resolve().parents[1]


def sft_row(split="train", license="MIT", verdict="yes", training_ok=True):
    return {
        "split": split,
        "source": {"dataset": "gsm8k", "license": license, "training": training_ok},
        "output_terms": {"m": {"verdict": verdict, "licence": "Apache-2.0"}},
    }


def test_licence_checks_on_training_rows():
    findings, summary = training.check_rows("sft", [sft_row(), sft_row(split="test", license="")])
    assert findings == []  # test rows are not training data
    assert summary["sources"] == {"gsm8k": {"rows": 1, "license": "MIT"}}
    for bad, words in [
        (sft_row(license=""), "no source licence"),
        (sft_row(license="CC-BY-SA-3.0"), "may not be in a training split"),
        (sft_row(license="CC-BY-NC-4.0"), "may not be in a training split"),
        (sft_row(training_ok=False), "may not be in a training split"),
        (sft_row(verdict="unclear"), "is not 'yes'"),
    ]:
        findings, _ = training.check_rows("sft", [bad])
        assert [f.level for f in findings] == ["error"] and words in findings[0].text


def test_laya_rows_are_checked_through_their_factors():
    row = {"split": "train", "factors": json.dumps(sft_row(verdict="no"))}
    findings, _ = training.check_rows("laya", [row])
    assert findings and "not 'yes'" in findings[0].text


def test_mix_size_and_heldout_findings():
    counts = {k: {"train": 10, "test": 3} for k in ("laya", "sft", "pairs")}
    findings = training.pack_findings(
        counts, {"public_share": 0.1, "self_share": 0.5}, 0.3, 0.3, {"math": 3}, 50
    )
    levels = [f.level for f in findings]
    assert levels.count("mix") == 2 and "error" not in levels
    assert any("held-out" in f.text for f in findings)
    empty = {k: {"train": 0, "test": 0} for k in counts}
    assert (
        training.pack_findings(empty, {"public_share": 0, "self_share": 0}, 0.3, 0.3, {}, 0)[
            0
        ].level
        == "error"
    )


async def test_prepare_packs_exports_kit_and_manifest(tmp_path):
    engine, _ = await logged()
    all_yes(engine)
    out = tmp_path / "pack"
    report = training.prepare(engine, out, test_percent=0, users={"local"})
    # Tempo's own traffic only: no public data, so the mix check stops packing...
    assert report.zip_path is None and report.mix_problems and not report.errors
    # ...unless the owner forces it, which the manifest records.
    report = training.prepare(engine, out, test_percent=0, users={"local"}, force=True)
    assert report.zip_path is not None and report.manifest["forced"]
    names = set(zipfile.ZipFile(report.zip_path).namelist())
    assert {"tempo-pack.json", "tempo_trainkit.py", "sft/train.jsonl", "pairs/train.jsonl"} <= names
    assert {"laya/train.jsonl", "laya/README.md"} <= names
    manifest = json.loads((out / "tempo-pack.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["sft"]["train"] >= 1
    assert manifest["licences"]["sft"]["sources"]["tempo-traffic"]["rows"] >= 1
    assert manifest["files"]["tempo_trainkit.py"] == training._sha256(out / "tempo_trainkit.py")
    for name in training.NOTEBOOKS:
        assert (tmp_path / "notebooks" / name).exists()
    steps = training.upload_steps(report)
    assert str(report.zip_path) in steps and "models import" in steps


def test_committed_notebooks_match_the_generator():
    for name in notebooks.NOTEBOOKS:
        committed = (ROOT / "training" / name).read_text(encoding="utf-8")
        assert committed == notebooks.render(name), f"run `tempo-server train notebooks` ({name})"


def test_notebook_code_compiles_and_uses_the_kit():
    for name, cells in notebooks.NOTEBOOKS.items():
        code = [text for kind, text in cells if kind == "code"]
        for source in code:
            compile(source, name, "exec")
        assert any("import tempo_trainkit as tk" in c for c in code)
        assert not any(
            line.lstrip().startswith(("!", "%")) for c in code for line in c.splitlines()
        )
        nb = json.loads(notebooks.render(name))
        assert nb["nbformat"] == 4 and nb["metadata"]["kaggle"]["accelerator"]


def test_the_kit_is_standalone_and_pins_its_downloads():
    tree = ast.parse(training.kit_source().read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "tempo" not in imported  # it runs on Kaggle without Tempo-server installed
    assert len(trainkit.LLAMA_CPP["linux-x86_64"]["sha256"]) == 64
    assert len(trainkit.LLAMA_CPP["commit"]) == 40
    for base in trainkit.CORE_BASES.values():
        assert base["licence"] in ("Apache-2.0", "MIT") and base["source"] and base["checked"]
    assert trainkit.LAYA_BASE["licence"] == "Apache-2.0"


def test_kit_helpers(tmp_path):
    state = trainkit.State(tmp_path)
    state.mark("sft", "incomplete", reason="time")
    assert not trainkit.State(tmp_path).done("sft")
    trainkit.State(tmp_path).mark("sft", "done", steps=3)
    assert trainkit.State(tmp_path).get("sft") == {"status": "done", "reason": "time", "steps": 3}
    for n in (5, 40, 7):
        (tmp_path / "ck" / f"checkpoint-{n}").mkdir(parents=True)
    assert trainkit.latest_checkpoint(tmp_path / "ck").name == "checkpoint-40"
    assert trainkit.fmt_s(3725) == "1h02m" and trainkit.fmt_s(65) == "1m05s"
    modelfile = trainkit.modelfile("x.gguf")
    assert modelfile.startswith("FROM ./x.gguf") and "<think>" in modelfile


def test_resume_copies_an_earlier_runs_checkpoints(tmp_path):
    earlier = tmp_path / "input" / "earlier-version" / "checkpoints"
    trainkit.State(earlier.parent).mark("sft", "done")
    work = tmp_path / "work"
    assert trainkit.resume_from_inputs(tmp_path / "input", work)
    assert trainkit.State(work).done("sft")
    assert not trainkit.resume_from_inputs(tmp_path / "nothing", tmp_path / "fresh")


def test_score_laya_counts_per_decision():
    rows = [
        {
            "state": "{}",
            "questions": json.dumps(
                {
                    "strategy": {"type": "choice", "criteria": {"a": "", "b": ""}},
                    "difficulty": {"type": "score", "criteria": ["0", "1", "2", "3"]},
                }
            ),
            "gold": json.dumps({"strategy": {"label": "a"}, "difficulty": {"label": "2"}}),
        }
    ]

    def predict(state, questions):
        return {
            "strategy": {"choice": "a"},
            "difficulty": {"probabilities": {"0": 0.1, "1": 0.7, "2": 0.2, "3": 0.0}},
        }

    scores = trainkit.score_laya(predict, rows)
    assert scores["decisions"]["strategy"] == {"n": 1, "accuracy": 1.0}
    assert scores["decisions"]["difficulty"] == {"n": 1, "accuracy": 0.0}
    assert scores["accuracy"] == 0.5


def test_dry_run_says_what_to_install(tmp_path, monkeypatch):
    import pytest

    from tempo import dry_run

    monkeypatch.setattr(dry_run, "missing_packages", lambda: ["torch", "trl"])
    with pytest.raises(dry_run.DryRunError, match=r"tempo-server\[train\]"):
        dry_run.run(tmp_path)
