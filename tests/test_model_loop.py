"""tempo-server models import / compare / promote, with a fake Ollama and fake runners."""

import hashlib
import json
import sys
import zipfile

import httpx
import pytest
from conftest import make_engine
from typer.testing import CliRunner

from tempo import model_loop
from tempo.analyzer import analyze
from tempo.router import Router
from tempo.types import ModelInfo, ProviderInfo


def core_output(tmp_path, *, status="complete", run_id="20260929-1200", corrupt=False):
    """A folder shaped like the Tempo-Core notebook's output, zipped as Kaggle downloads it."""
    out = tmp_path / "kaggle" / "tempo-core"
    out.mkdir(parents=True)
    gguf = out / "tempo-core-q4_k_m.gguf"
    gguf.write_bytes(b"GGUF fake weights")
    digest = hashlib.sha256(b"GGUF fake weights").hexdigest()
    report = {
        "model": "Tempo-Core",
        "status": status,
        "run_id": run_id,
        "base": "Qwen/Qwen3-1.7B",
        "ollama_base": "qwen3:1.7b",
        "heldout": {"sft_loss_after": 1.2},
        "files": {gguf.name: {"bytes": 17, "sha256": "0" * 64 if corrupt else digest}},
    }
    (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (out / "Modelfile").write_text("FROM ./tempo-core-q4_k_m.gguf\n", encoding="utf-8")
    archive = tmp_path / "output.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for path in out.rglob("*"):
            z.write(path, f"tempo-core/{path.name}")
    return archive, digest


class FakeOllama:
    def __init__(self):
        self.blobs, self.created = set(), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/blobs/"):
            digest = path.rsplit("/", 1)[1]
            if request.method == "HEAD":
                return httpx.Response(200 if digest in self.blobs else 404)
            assert "sha256:" + hashlib.sha256(request.content).hexdigest() == digest
            self.blobs.add(digest)
            return httpx.Response(201)
        if path == "/api/create":
            body = json.loads(request.content)
            assert set(body["files"].values()) <= self.blobs
            self.created.append(body)
            return httpx.Response(200, json={"status": "success"})
        if path == "/api/chat":
            body = json.loads(request.content)
            if "think" in body:
                return httpx.Response(
                    400, json={"error": f"{body['model']} does not support think"}
                )
            return httpx.Response(
                200,
                json={
                    "message": {"content": f"answer from {body['model']}"},
                    "eval_count": 20,
                    "eval_duration": 1_000_000_000,
                },
            )
        return httpx.Response(404)


def test_import_registers_tempo_core_with_ollama(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    archive, digest = core_output(tmp_path)
    ollama = FakeOllama()
    results = model_loop.import_model(engine, archive, transport=httpx.MockTransport(ollama))
    (item,) = results
    assert item.kind == "tempo-core" and item.version == "20260929-1200"
    assert item.ollama == "tempo-core:20260929-1200"
    (created,) = ollama.created
    assert created["model"] == "tempo-core:20260929-1200"
    assert created["files"] == {"tempo-core-q4_k_m.gguf": f"sha256:{digest}"}
    assert "Apache License, Version 2.0" in created["license"]  # so Tempo reads it as "yes"
    assert "<|im_start|>" in created["template"]
    assert (item.path / "tempo-core-q4_k_m.gguf").exists() and (item.path / "Modelfile").exists()
    (entry,) = model_loop.imported(engine, "tempo-core")
    assert entry["ollama"] == "tempo-core:20260929-1200" and entry["ollama_base"] == "qwen3:1.7b"


def test_import_without_ollama_says_how(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    archive, _ = core_output(tmp_path)

    def refuse(request):
        raise httpx.ConnectError("refused")

    (item,) = model_loop.import_model(engine, archive, transport=httpx.MockTransport(refuse))
    assert item.ollama is None and "ollama create tempo-core:" in item.notes[0]


@pytest.mark.parametrize(
    "kwargs, words", [({"corrupt": True}, "checksum"), ({"status": "incomplete"}, "incomplete")]
)
def test_import_refuses_bad_outputs(tmp_path, kwargs, words):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    archive, _ = core_output(tmp_path, **kwargs)
    with pytest.raises(model_loop.LoopError, match=words):
        model_loop.import_model(engine, archive, ollama=False)


def test_import_refuses_paths_that_escape(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escape.gguf", b"x")
    with pytest.raises(model_loop.LoopError, match="unsafe"):
        model_loop.import_model(engine, archive, ollama=False)


def test_import_laya_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "laya", None)  # not installed: copied, not test-loaded
    engine, _ = make_engine(data_dir=tmp_path / "data")
    out = tmp_path / "tempo-router-judge"
    (out / "encoder").mkdir(parents=True)
    (out / "tokenizer").mkdir()
    (out / "encoder" / "config.json").write_text("{}", encoding="utf-8")
    (out / "model.safetensors").write_bytes(b"w")
    (out / "rl_agent_config.json").write_text("{}", encoding="utf-8")
    (out / "report.json").write_text(
        json.dumps({"status": "complete", "run_id": "20260929-0900"}), encoding="utf-8"
    )
    (item,) = model_loop.import_model(engine, out)
    assert item.kind == "laya" and (item.path / "model.safetensors").exists()
    assert "not test-loaded" in item.notes[0]


def pair(task, old, new):
    return {
        "task_type": task,
        "old_score": old,
        "new_score": new,
        "old_answer": "a b c",
        "new_answer": "a b d",
    }


def test_gate_per_task_type():
    pairs = [pair("math", 0.5, 0.8)] * 30 + [pair("code", 0.9, 0.6)] * 30 + [pair("chat", 0, 1)]
    types = {t.task: t for t in model_loop.per_type(pairs, 30)}
    assert types["math"].takes_over and types["math"].wins == 30
    assert not types["code"].takes_over and "dropped" in types["code"].reason
    assert not types["chat"].takes_over and "only 1" in types["chat"].reason
    same = {"distinct_2": 0.9, "repeated_4": 0.0}
    ok, reasons = model_loop.release_check(
        pairs, list(types.values()), {"old": same, "new": same}, 20.0, 8.0
    )
    assert ok, reasons
    slow, reasons = model_loop.release_check(
        pairs, list(types.values()), {"old": same, "new": same}, 3.0, 8.0
    )
    assert not slow and "below 8" in reasons[-1]
    repetitive = {"distinct_2": 0.5, "repeated_4": 0.3}
    assert not model_loop.release_check(
        pairs, list(types.values()), {"old": same, "new": repetitive}, 20.0, 8.0
    )[0]
    ties = [pair("math", 0.5, 0.5)] * 30
    tie_types = model_loop.per_type(ties, 30)
    ok, reasons = model_loop.release_check(ties, tie_types, {"old": same, "new": same}, 20, 8)
    assert tie_types[0].takes_over and not ok and "needs more wins" in reasons[0]


def test_bootstrap_low_is_below_the_mean():
    diffs = [0.1, 0.2, -0.05, 0.3, 0.0] * 10
    assert 0 < model_loop.bootstrap_low(diffs) < sum(diffs) / len(diffs)
    assert model_loop.bootstrap_low([]) == 0.0


class FakeRunner:
    def __init__(self, label, score):
        self.label, self.score = label, score

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def answer(self, messages):
        return model_loop.Reply(f"{self.label}|{messages[-1]['content']}", 25.0)


def write_pack(tmp_path, n_math=30, n_chat=2):
    pack = tmp_path / "pack"
    (pack / "sft").mkdir(parents=True)
    rows = [
        {
            "question_id": f"q{i}",
            "task_type": "math" if i < n_math else "chat",
            "split": "test",
            "messages": [
                {"role": "user", "content": f"What is {i} + {i}?"},
                {"role": "assistant", "content": str(2 * i)},
            ],
        }
        for i in range(n_math + n_chat)
    ]
    (pack / "sft" / "test.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    return pack


def test_compare_then_promote_routes_per_task_type(tmp_path, monkeypatch):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    archive, _ = core_output(tmp_path)
    model_loop.import_model(engine, archive, transport=httpx.MockTransport(FakeOllama()))
    scores = {"old": 0.5, "new": 0.8}
    monkeypatch.setattr(
        model_loop,
        "core_runner",
        lambda engine, spec, *a, **k: FakeRunner("new" if spec == "latest" else "old", 0),
    )

    async def fake_grade(engine, question, answer, family):
        return scores[answer.split("|")[0]]

    monkeypatch.setattr(model_loop, "grade", fake_grade)
    result = model_loop.compare_core(engine, write_pack(tmp_path), say=lambda s: None)
    assert result["old"] == "base" and result["new_version"] == "20260929-1200"
    assert result["release"] and result["promote"] == ["math"]  # chat: too few questions
    assert result["tokens_per_second"] == 25.0
    change = model_loop.promote(engine, "tempo-core")
    assert change["routes"] == {"math": "tempo-core:20260929-1200"}
    assert model_loop.load_routes(tmp_path / "data") == change["routes"]
    # The registry restricts that Ollama model to the task types it won.
    engine.registry.providers["ollama"] = ProviderInfo(id="ollama", label="Ollama", local=True)
    engine.registry._models["ollama_chat/tempo-core:20260929-1200"] = ModelInfo(
        id="ollama_chat/tempo-core:20260929-1200", provider="ollama", name="t", installed=True
    )
    engine.registry.apply_core_routes()
    model = engine.registry.get("ollama_chat/tempo-core:20260929-1200")
    assert model.tasks == ["math"]
    # The router skips a model for task types it was not promoted for.
    router = Router(engine.registry, engine.health)
    chat = analyze([{"role": "user", "content": "hi there!"}])
    other = next(m for m in engine.registry.all() if router.skip_reason(m, chat) is None)
    other.tasks = ["math"]
    assert "not promoted for chat" in router.skip_reason(other, chat)
    assert router.skip_reason(other, chat, explicit=True) is None
    # A later engine reads the routes from the data folder.
    later, _ = make_engine(data_dir=tmp_path / "data")
    assert later.registry.core_routes == change["routes"]


def test_promote_refuses_a_failed_gate(tmp_path):
    engine, _ = make_engine(data_dir=tmp_path / "data")
    data = model_loop.book(engine)
    data["compare"]["tempo-core"] = {"release": False, "reasons": ["no task type passed"]}
    model_loop._save_book(engine, data)
    change = model_loop.promote(engine, "tempo-core")
    assert change["changed"] is False and model_loop.load_routes(tmp_path / "data") == {}
    with pytest.raises(model_loop.LoopError, match="compare"):
        model_loop.promote(engine, "laya")


def test_promote_laya_sets_the_checkpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("TEMPO_LAYA_MODEL", raising=False)
    engine, _ = make_engine(data_dir=tmp_path / "data")
    data = model_loop.book(engine)
    data["compare"]["laya"] = {"release": True, "reasons": [], "new_path": "/models/laya/v1"}
    model_loop._save_book(engine, data)
    change = model_loop.promote(engine, "laya")
    assert change["laya_model"] == "/models/laya/v1"
    settings = (tmp_path / "data" / "settings.env").read_text(encoding="utf-8")
    assert "TEMPO_LAYA_MODEL=/models/laya/v1" in settings
    monkeypatch.delenv("TEMPO_LAYA_MODEL", raising=False)


def test_ollama_runner_retries_without_think():
    runner = model_loop.OllamaRunner(
        "http://ollama", "tempo-core:v1", 32, transport=httpx.MockTransport(FakeOllama())
    )
    with runner:
        reply = runner.answer([{"role": "user", "content": "hi"}])
    assert reply.text == "answer from tempo-core:v1" and reply.tokens_per_second == 20.0


def test_cli_explains_missing_steps(tmp_path, monkeypatch):
    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TEMPO_ENABLE_MOCK", "1")
    result = CliRunner().invoke(app, ["models", "promote"])
    assert result.exit_code == 1 and "compare" in result.output
    result = CliRunner().invoke(app, ["models", "import", str(tmp_path / "missing.zip")])
    assert result.exit_code == 1 and "does not exist" in result.output
