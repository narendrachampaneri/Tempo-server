"""Laya's CPU runtime: which checkpoint, where the ONNX export is cached, and when it is rebuilt.
(The export itself needs PyTorch and Laya; it was checked by hand, see docs/LAYA_CPU.md.)"""

import os

from tempo import laya_runtime


def checkpoint(tmp_path, name="ckpt", weights=b"w" * 10):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(weights)
    (folder / "rl_agent_config.json").write_text("{}", encoding="utf-8")
    return folder


def test_a_local_folder_is_used_as_it_is(tmp_path):
    folder = checkpoint(tmp_path)
    assert laya_runtime.checkpoint_dir("english", str(folder)) == folder.resolve()


def test_cache_key_follows_the_weights(tmp_path):
    folder = checkpoint(tmp_path)
    first = laya_runtime.cache_key(folder)
    assert first == laya_runtime.cache_key(folder)
    (folder / "model.safetensors").write_bytes(b"retrained weights")
    assert laya_runtime.cache_key(folder) != first


def test_onnx_is_exported_once_and_the_fp32_copy_removed(tmp_path, monkeypatch):
    folder = checkpoint(tmp_path)
    calls = []

    def fake_export(model_dir, out):
        calls.append("export")
        out.write_bytes(b"fp32 graph")
        return out

    def fake_quantize(fp32, out):
        calls.append("quantize")
        assert fp32.read_bytes() == b"fp32 graph"
        out.write_bytes(b"int8 graph")
        return out

    monkeypatch.setattr(laya_runtime, "export_onnx", fake_export)
    monkeypatch.setattr(laya_runtime, "quantize_int8", fake_quantize)
    cache = tmp_path / "cache"
    path = laya_runtime.onnx_file(folder, cache, int8=True)
    assert path.read_bytes() == b"int8 graph" and calls == ["export", "quantize"]
    assert not (path.parent / "laya.onnx").exists()  # the 3x larger fp32 copy is not kept
    assert (path.parent / "export.json").exists()

    assert laya_runtime.onnx_file(folder, cache, int8=True) == path
    assert calls == ["export", "quantize"]  # cached: nothing rebuilt

    fp32 = laya_runtime.onnx_file(folder, cache, int8=False)
    assert fp32.name == "laya.onnx" and calls[-1] == "export"


def test_threads_stay_between_one_and_four():
    assert 1 <= laya_runtime.default_threads() <= min(4, os.cpu_count() or 1)
