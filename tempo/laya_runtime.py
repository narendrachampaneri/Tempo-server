"""Laya on an ordinary CPU. Software only, no GPU.

Three backends, all CPU (measured in docs/LAYA_CPU.md):

- ``torch`` (default): PyTorch fp32. The reference answers; loads in seconds.
- ``onnx``: ONNX Runtime fp32. Identical answers, no faster, more memory.
- ``onnx-int8``: per-channel INT8 weights, as Laya's own ``scripts/export_onnx.py --quantize``.
  About twice as fast on short inputs, but it changes answers: on a fine-tuned checkpoint its
  accuracy fell from 0.767 to 0.683. Opt in only after checking it on your own decisions.

ONNX backends export the checkpoint once (a minute or two) and cache the file. The export uses
the TorchScript exporter with dynamic batch and sequence axes: the default (dynamo) exporter in
recent PyTorch fixes one sequence dimension, and that graph fails on longer inputs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STOCK_REPO = "convaiinnovations/laya"
STOCK_SUBFOLDER = {"english": None, "multilingual": "multilingual"}
CHECKPOINT_FILES = ("rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*")
BACKENDS = ("onnx-int8", "onnx", "torch")
FORMAT_VERSION = 1  # bump when the export recipe changes, to rebuild cached files


def checkpoint_dir(stock: str, model: str | None) -> Path:
    """The local folder of the checkpoint to load: a stock one, or a tuned one (folder or Hub)."""
    if model and Path(model).expanduser().is_dir():
        return Path(model).expanduser().resolve()
    from huggingface_hub import snapshot_download

    if model:
        return Path(snapshot_download(model, allow_patterns=list(CHECKPOINT_FILES)))
    sub = STOCK_SUBFOLDER[stock]
    prefix = f"{sub}/" if sub else ""
    root = Path(
        snapshot_download(STOCK_REPO, allow_patterns=[prefix + f for f in CHECKPOINT_FILES])
    )
    return root / sub if sub else root


def cache_key(model_dir: Path) -> str:
    weights = model_dir / "model.safetensors"
    stat = weights.stat()
    raw = f"{model_dir.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{FORMAT_VERSION}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def export_onnx(model_dir: Path, out: Path) -> Path:
    """Export a Laya checkpoint to ONNX (fp32), with dynamic batch and sequence length."""
    import torch
    from laya.agent import Agent

    agent = Agent(str(model_dir), compile=False, device="cpu")
    agent.model.eval()
    fastpath = torch.backends.mha.get_fastpath_enabled()
    torch.backends.mha.set_fastpath_enabled(False)  # its fused layer op has no ONNX export
    seq, markers = 48, 3
    inputs = (
        torch.randint(5, 1000, (2, seq), dtype=torch.long),
        torch.ones((2, seq), dtype=torch.long),
        torch.tensor([[1, 5, 9], [2, 6, 10]], dtype=torch.long),
        torch.ones((2, markers), dtype=torch.bool),
        torch.tensor([0, 1], dtype=torch.long),
    )
    axes = {
        "input_ids": {0: "batch", 1: "seq"},
        "attention_mask": {0: "batch", 1: "seq"},
        "marker_pos": {0: "batch", 1: "markers"},
        "marker_mask": {0: "batch", 1: "markers"},
        "qtype": {0: "batch"},
        "logits": {0: "batch", 1: "markers"},
        "act_logits": {0: "batch"},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with torch.no_grad():
            torch.onnx.export(
                agent.model,
                inputs,
                str(out),
                export_params=True,
                opset_version=17,
                do_constant_folding=True,
                input_names=["input_ids", "attention_mask", "marker_pos", "marker_mask", "qtype"],
                output_names=["logits", "act_logits"],
                dynamic_axes=axes,
                dynamo=False,
            )
    finally:
        torch.backends.mha.set_fastpath_enabled(fastpath)
    return out


def quantize_int8(fp32: Path, out: Path) -> Path:
    """INT8 weights for every MatMul, one scale per output channel; activations stay fp32.
    Per channel matters: per tensor flipped decisions in Laya's own measurements."""
    import onnx
    from onnxruntime.quantization import QuantType, quantize_dynamic

    model = onnx.load(str(fp32))
    del model.graph.value_info[:]  # exporter shape hints clash with the quantizer's own
    quantize_dynamic(
        model_input=model,
        model_output=str(out),
        op_types_to_quantize=["MatMul"],
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    return out


def onnx_file(model_dir: Path, cache_dir: Path, int8: bool) -> Path:
    """The cached ONNX file for this checkpoint, built on first use."""
    folder = cache_dir / cache_key(model_dir)
    target = folder / ("laya.int8.onnx" if int8 else "laya.onnx")
    if target.exists():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    log.info("Exporting Laya to ONNX for CPU (once; this takes a minute or two)")
    fp32 = folder / "laya.onnx"
    if not fp32.exists():
        partial = folder / "laya.onnx.part"
        export_onnx(model_dir, partial)
        partial.replace(fp32)
    if int8:
        partial = folder / "laya.int8.onnx.part"
        quantize_int8(fp32, partial)
        partial.replace(target)
        fp32.unlink()  # 3x bigger and no faster than PyTorch on CPU; not kept
    (folder / "export.json").write_text(
        json.dumps({"checkpoint": str(model_dir), "int8": int8, "format": FORMAT_VERSION})
    )
    log.info("Laya ONNX export ready in %.0fs", time.perf_counter() - started)
    return target


@dataclass
class OnnxRunner:
    agent: Any

    def predict(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return self.agent.system_one(state, questions)


@dataclass
class TorchRunner:
    agent: Any

    def predict(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return self.agent.system_one(state, questions)


def load(
    *,
    backend: str,
    stock: str,
    model: str | None,
    threads: int,
    cache_dir: Path,
    device: str | None = None,
) -> OnnxRunner | TorchRunner:
    """Load one Laya checkpoint for CPU inference with ``threads`` threads."""
    model_dir = checkpoint_dir(stock, model)
    if backend == "torch":
        import torch
        from laya.agent import Agent

        torch.set_num_threads(threads)
        return TorchRunner(Agent(str(model_dir), device=device or "cpu"))
    import onnxruntime as ort
    from laya.onnx_agent import ONNXAgent

    path = onnx_file(model_dir, cache_dir, int8=backend == "onnx-int8")
    agent = ONNXAgent(str(model_dir), onnx_path=str(path))
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    agent.session = ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return OnnxRunner(agent)


def default_threads() -> int:
    """Physical cores are what matter; more threads than about four do not help one request."""
    return max(1, min(4, os.cpu_count() or 1))
