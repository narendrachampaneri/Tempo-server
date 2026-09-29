"""`tempo record-demo`: the recording the static demo page replays."""

import json

from typer.testing import CliRunner

from tempo.cli import app


def test_record_demo_writes_a_replayable_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPO_DATA_DIR", "memory")
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    monkeypatch.setenv("TEMPO_ENABLE_MOCK", "1")
    monkeypatch.setenv("TEMPO_CACHE", "off")
    out = tmp_path / "recording.js"
    result = CliRunner().invoke(app, ["record-demo", "--out", str(out), "-q", "What is 2+2?"])
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert text.startswith("window.TEMPO_RECORDING = ")
    recording = json.loads(text.removeprefix("window.TEMPO_RECORDING = ").rstrip(";\n"))
    assert recording["demo_mode"] is True and len(recording["sessions"]) == 1
    types = [e["type"] for e in recording["sessions"][0]["events"]]
    assert {"plan", "stage_start", "answer_delta", "done"} <= set(types)
    assert "question_id" not in text and '"quota"' not in text


def test_the_committed_demo_page_has_a_recording():
    from pathlib import Path

    docs = Path(__file__).parent.parent / "docs" / "demo"
    assert 'src="recording.js"' in (docs / "index.html").read_text(encoding="utf-8")
    assert (
        (docs / "recording.js").read_text(encoding="utf-8").startswith("window.TEMPO_RECORDING = ")
    )
