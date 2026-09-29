"""Laya speed (step 10): the faster runner is measured and kept, Laya is skipped when the rules
are sure, doctor-facing status, and no checkpoint download without the Laya extra. Fake
runners only (the real Laya is optional and not installed in CI's main jobs)."""

import json

import pytest
from conftest import make_engine, user
from test_laya import FakeLaya

from tempo import laya_runtime
from tempo.config import Settings
from tempo.laya_decider import LayaDecider


def decider(tmp_path, torch_delay=0.03, onnx_delay=0.005, onnx=True, memory=16.0, **kw):
    runners = {"torch": FakeLaya(delay=torch_delay), "onnx": FakeLaya(delay=onnx_delay)}
    loaded = []

    def loader(name):
        def load():
            loaded.append(name)
            return runners[name]

        return load

    settings = Settings(data_dir=tmp_path, **kw)
    d = LayaDecider(
        settings,
        loaders={name: loader(name) for name in ("torch", "onnx", "onnx-int8")},
        onnx_available=lambda: onnx,
        memory_gb=lambda: memory,
    )
    return d, runners, loaded


def test_auto_times_both_fp32_runners_once_and_keeps_the_faster(tmp_path):
    d, runners, loaded = decider(tmp_path)
    d._load()
    assert d.status == "ready" and d.backend == "torch"  # serves at once with PyTorch
    d.comparing.result(timeout=30)
    assert d.backend == "onnx" and d._runner is runners["onnx"]
    assert "faster than torch" in d.runner_note
    saved = json.loads((tmp_path / "laya" / "runner.json").read_text(encoding="utf-8"))
    entry = next(iter(saved.values()))
    assert entry["backend"] == "onnx" and set(entry["ms"]) == {"torch", "onnx"}
    # the next start loads the measured runner straight away, with no second comparison
    again, _, loaded_again = decider(tmp_path)
    again._load()
    assert again.backend == "onnx" and loaded_again == ["onnx"] and again.comparing is None


def test_auto_keeps_pytorch_when_onnx_is_not_faster(tmp_path):
    d, runners, _ = decider(tmp_path, torch_delay=0.005, onnx_delay=0.03)
    d._load()
    d.comparing.result(timeout=30)
    assert d.backend == "torch" and d._runner is runners["torch"]
    assert "the time of torch" in d.runner_note


@pytest.mark.parametrize(
    ("onnx", "memory", "why"),
    [(False, 16.0, "not installed"), (True, 8.0, "too little to time both")],
)
def test_auto_explains_why_it_stays_on_pytorch(tmp_path, onnx, memory, why):
    d, _, loaded = decider(tmp_path, onnx=onnx, memory=memory)
    d._load()
    d.comparing.result(timeout=30)
    assert d.backend == "torch" and loaded == ["torch"] and why in d.runner_note
    assert not (tmp_path / "laya" / "runner.json").exists()  # tried again once things change


def test_a_set_backend_is_used_as_is(tmp_path):
    d, _, loaded = decider(tmp_path, laya_backend="onnx")
    d._load()
    assert d.backend == "onnx" and loaded == ["onnx"] and d.comparing is None
    assert "TEMPO_LAYA_BACKEND" in d.runner_note


def test_status_for_doctor_names_the_runner_and_time_per_decision(tmp_path):
    d, _, _ = decider(tmp_path)
    d._load()
    d.comparing.result(timeout=30)
    status = laya_runtime.read_status(tmp_path / "laya")
    assert status["status"] == "ready" and status["backend"] == "onnx"
    assert set(status["ms"]) == {"plan", "assess", "pick"}
    assert status["runner_note"].startswith("onnx")


def with_sure_skip(fake):
    engine, backend = make_engine(judge_score=9)
    settings = Settings(laya_timeout_ms=500)  # skipping when sure is the default
    engine.laya = LayaDecider(settings, engine.store, loader=lambda: fake)
    engine.laya._load()
    fake.calls.clear()
    return engine, backend


def asked(fake, group_question):
    return [q for _, questions in fake.calls for q in questions if q == group_question]


async def test_laya_is_not_asked_when_the_rules_are_sure():
    fake = FakeLaya()
    engine, _ = with_sure_skip(fake)
    result = await engine.complete(user("Write a Python function that reverses a string"))
    await engine.laya.drain()
    assert result.error is None
    assert not asked(fake, "task_type")  # an obvious code question
    assert not asked(fake, "quality")  # the judge gave 9/10: a clear pass
    rows = engine.store.query("SELECT DISTINCT laya_status FROM decisions")
    assert {"sure"} <= {r["laya_status"] for r in rows}


async def test_laya_is_asked_when_the_rules_are_unsure():
    fake = FakeLaya()
    engine, _ = with_sure_skip(fake)
    question = (
        "My neighbour keeps parking in front of my gate every evening and I am not sure what "
        "the right thing to do here would be, honestly."
    )
    await engine.complete(user(question))
    await engine.laya.drain()
    assert asked(fake, "task_type")  # a long request with no keyword saying what kind it is


def test_nothing_is_downloaded_without_the_laya_extra(monkeypatch, tmp_path):
    import huggingface_hub

    def no_download(*args, **kwargs):
        raise AssertionError("tried to download the checkpoint")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", no_download)
    monkeypatch.setattr(
        laya_runtime,
        "ensure_installed",
        lambda backend: (_ for _ in ()).throw(ImportError("laya is missing. Install: pip ...")),
    )
    with pytest.raises(ImportError, match="Install"):
        laya_runtime.load(
            backend="torch", stock="english", model=None, threads=1, cache_dir=tmp_path
        )


def test_the_missing_extra_names_the_install_command():
    with pytest.raises(ImportError) as info:
        laya_runtime.ensure_installed("torch")  # Laya isn't installed in this test run
    assert "laya[onnx]" in str(info.value)


def test_files_on_disk_are_used_without_a_network_check(monkeypatch, tmp_path):
    import huggingface_hub

    folder = tmp_path / "snap"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"x")
    calls = []

    def snapshot(repo, allow_patterns=None, local_files_only=False):
        calls.append(local_files_only)
        return str(folder)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setattr(laya_runtime, "download_size", lambda *a: pytest.fail("network used"))
    assert laya_runtime.checkpoint_dir("english", None) == folder
    assert calls == [True]  # only the local lookup


def test_a_download_says_what_and_how_big_first(monkeypatch, tmp_path):
    import huggingface_hub

    order = []

    def snapshot(repo, allow_patterns=None, local_files_only=False):
        if local_files_only:
            raise FileNotFoundError("not in the cache")
        order.append("download")
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    monkeypatch.setattr(laya_runtime, "download_size", lambda repo, patterns: 846_000_000)
    laya_runtime.checkpoint_dir("english", None, announce=lambda m: order.append(m))
    assert order[0] == (
        "Downloading the Laya checkpoint convaiinnovations/laya (english): 846 MB, once, "
        "into the Hugging Face cache"
    )
    assert order[1] == "download"


def test_install_command_puts_cpu_pytorch_first_on_linux_without_a_gpu(monkeypatch):
    monkeypatch.setattr(laya_runtime.sys, "platform", "linux")
    command = laya_runtime.install_command(gpu=False)
    assert command.startswith("pip install torch --index-url https://download.pytorch.org/whl/cpu")
    assert "laya[onnx]" in command
    monkeypatch.setattr(laya_runtime.sys, "platform", "darwin")
    assert "download.pytorch.org" not in laya_runtime.install_command(gpu=False)


def test_doctor_laya_line(monkeypatch, tmp_path):
    from tempo.doctor import check_laya

    engine, _ = make_engine(data_dir=tmp_path)
    missing = check_laya(engine)  # Laya isn't installed in this test run
    assert missing.status == "info" and "Not installed" in missing.message
    assert "laya[onnx]" in missing.fix and "846 MB" in missing.message

    monkeypatch.setattr(laya_runtime, "ensure_installed", lambda backend: None)
    monkeypatch.setattr(laya_runtime, "local_checkpoint", lambda stock, model: None)
    fresh = check_laya(engine)
    assert "not downloaded yet" in fresh.message

    d, _, _ = decider(tmp_path)
    d._load()
    d.comparing.result(timeout=30)
    ready = check_laya(engine)
    assert ready.status == "ok"
    assert ready.message.startswith("Runner onnx (onnx: measured")
    assert "plan" in ready.message and "pick" in ready.message and " ms" in ready.message

    assert check_laya(make_engine(laya="off")[0]).message.startswith("Off")


async def test_the_local_model_is_warmed_up_at_server_start():
    import httpx
    from conftest import ENV_ALL, ScriptedBackend, make_registry

    from tempo.engine import Engine
    from tempo.health import HealthTracker
    from tempo.types import ModelInfo, ProviderInfo

    registry = make_registry({**ENV_ALL, "OLLAMA_API_BASE": "http://ollama.test"})
    registry.providers["ollama"] = ProviderInfo(
        id="ollama", label="Ollama", base_env="OLLAMA_API_BASE", local=True
    )
    registry.add(
        ModelInfo(id="ollama_chat/qwen3:1.7b", provider="ollama", name="Qwen3", installed=True)
    )
    registry.add(
        ModelInfo(id="ollama_chat/absent", provider="ollama", name="Absent", installed=False)
    )
    engine = Engine(registry, lambda m: ScriptedBackend(), health=HealthTracker())
    seen = []

    def ollama(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"done": True})

    engine._http_transport = httpx.MockTransport(ollama)
    assert await engine.warm_local_model() == "ollama_chat/qwen3:1.7b"
    assert seen == [
        ("http://ollama.test/api/generate", {"model": "qwen3:1.7b", "keep_alive": "15m"})
    ]


async def test_no_local_model_means_no_warm_up():
    engine, _ = make_engine()
    assert await engine.warm_local_model() is None  # no Ollama provider configured


# Laya's own message (laya/agent.py) for the stock English checkpoint, as seen on the laptop.
STOCK_WARNING = (
    "laya: this checkpoint ships invalid temperatures or values outside [0.5, 5]; using "
    "choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as "
    "uncalibrated."
)


def test_the_stock_checkpoints_temperature_warning_is_explained_not_shouted():
    note = laya_runtime.explain_temperature_warning(STOCK_WARNING)
    assert note.startswith("Laya's checkpoint ships a temperature below Laya's own minimum")
    assert "choice:11+=0.10" in note and "never asks a choice with 11 or more options" in note
    # an entry Tempo does use stays a real warning
    used = STOCK_WARNING.replace("choice:11+=0.10058280825614929", "choice:6-10=0.2")
    assert laya_runtime.explain_temperature_warning(used) is None
    assert laya_runtime.explain_temperature_warning("something else") is None


def test_agent_warnings_other_than_the_known_one_still_reach_the_user(caplog):
    import logging
    import warnings

    def make():
        warnings.warn(STOCK_WARNING, RuntimeWarning, stacklevel=1)
        warnings.warn("laya: something new", RuntimeWarning, stacklevel=1)
        return "agent"

    with caplog.at_level(logging.INFO, logger="tempo.laya_runtime"):
        with pytest.warns(RuntimeWarning, match="something new") as seen:
            assert laya_runtime._agent(make) == "agent"
    assert all("invalid temperatures" not in str(w.message) for w in seen)
    assert "never asks a choice with 11 or more options" in caplog.text


def test_tempos_own_fine_tunes_stay_within_layas_temperature_range():
    from tempo.trainkit import LAYA_TEMP_MAX, LAYA_TEMP_MIN, laya_temperature

    assert (LAYA_TEMP_MIN, LAYA_TEMP_MAX) == (0.5, 5.0)
    assert laya_temperature(0.1006) == 0.5  # what our fit could write before (0.1 to 10)
    assert laya_temperature(9.0) == 5.0 and laya_temperature(1.3) == 1.3
