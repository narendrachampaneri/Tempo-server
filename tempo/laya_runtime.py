"""Laya on an ordinary CPU. Software only, no GPU.

Backends, all CPU (measured in docs/LAYA_CPU.md):

- ``auto`` (default): PyTorch fp32 first; when ONNX Runtime is installed too, both fp32
  runners are timed once on this machine and the faster one is used from then on (they give
  identical answers). The choice is saved in the data folder (laya/runner.json).
- ``torch``: PyTorch fp32. The reference answers; loads in seconds.
- ``onnx``: ONNX Runtime fp32. Identical answers; faster on some CPUs, not on others.
- ``onnx-int8``: per-channel INT8 weights, as Laya's own ``scripts/export_onnx.py --quantize``.
  About twice as fast on short inputs, but it changes answers: on a fine-tuned checkpoint its
  accuracy fell from 0.767 to 0.683. Opt in only after checking it on your own decisions.

ONNX backends export the checkpoint once (a minute or two) and cache the file. The export uses
the TorchScript exporter with dynamic batch and sequence axes: the default (dynamo) exporter in
recent PyTorch fixes one sequence dimension, and that graph fails on longer inputs.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STOCK_REPO = "convaiinnovations/laya"
STOCK_SUBFOLDER = {"english": None, "multilingual": "multilingual"}
CHECKPOINT_FILES = ("rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*")
BACKENDS = ("onnx-int8", "onnx", "torch")
FORMAT_VERSION = 1  # bump when the export recipe changes, to rebuild cached files
RUNNER_FILE = "runner.json"  # the runner measured fastest on this machine, per checkpoint
STATUS_FILE = "status.json"  # what the last load found: runner, times per decision
# Timing both fp32 runners holds both in memory for a moment (~2.8 + 4.5 GB, LAYA_CPU.md).
COMPARE_MIN_MEMORY_GB = 12.0


def install_command(gpu: bool | None = None) -> str:
    """The exact command that adds Laya to this install. On Linux without a GPU, PyTorch's CPU
    build first: the default wheel from PyPI brings about 2.5 GB of GPU libraries."""
    laya = '"laya[onnx]>=0.3.21"'
    if sys.platform.startswith("linux") and not gpu:
        return (
            "pip install torch --index-url https://download.pytorch.org/whl/cpu && "
            f"pip install {laya}   (a pipx install: pipx inject tempo-server torch "
            '--pip-args="--index-url https://download.pytorch.org/whl/cpu" && '
            f"pipx inject tempo-server {laya})"
        )
    return f"pip install {laya}   (a pipx install: pipx inject tempo-server {laya})"


def ensure_installed(backend: str) -> None:
    """Raise ImportError (with the install command) unless this backend's packages import.
    Checked before anything is downloaded, so the checkpoint is never fetched for nothing."""
    try:
        import laya  # noqa: F401

        if backend == "torch":
            import torch  # noqa: F401
        else:
            import onnxruntime  # noqa: F401
            from laya.onnx_agent import ONNXAgent  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            f"{exc.name or exc} is missing for Laya ({backend}). Install: {install_command()}"
        ) from exc


def onnx_available() -> bool:
    try:
        ensure_installed("onnx")
    except ImportError:
        return False
    return True


def _source(stock: str, model: str | None) -> tuple[str, list[str], str | None]:
    """(Hub repository, file patterns, subfolder) of the checkpoint to load."""
    if model:
        return model, list(CHECKPOINT_FILES), None
    sub = STOCK_SUBFOLDER[stock]
    prefix = f"{sub}/" if sub else ""
    return STOCK_REPO, [prefix + f for f in CHECKPOINT_FILES], sub


def local_checkpoint(stock: str, model: str | None) -> Path | None:
    """The checkpoint's folder when its files are already on disk, found without touching the
    network; else None."""
    if model and Path(model).expanduser().is_dir():
        return Path(model).expanduser().resolve()
    from huggingface_hub import snapshot_download

    repo, patterns, sub = _source(stock, model)
    try:
        root = Path(snapshot_download(repo, allow_patterns=patterns, local_files_only=True))
    except Exception:  # not in the cache yet (LocalEntryNotFoundError and friends)
        return None
    folder = root / sub if sub else root
    return folder if (folder / "model.safetensors").exists() else None


def download_size(repo: str, patterns: list[str]) -> int | None:
    """Bytes the checkpoint download will take, from the Hub's file list (None if unknown)."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo, files_metadata=True)
    except Exception:
        return None
    total = 0
    for sibling in info.siblings or []:
        if any(fnmatch.fnmatch(sibling.rfilename, p) for p in patterns):
            total += sibling.size or 0
    return total or None


def checkpoint_dir(
    stock: str, model: str | None, announce: Callable[[str], None] | None = None
) -> Path:
    """The local folder of the checkpoint to load: a stock one, or a tuned one (folder or Hub).
    Files already on disk are used without a network check; otherwise ``announce`` is told
    what will be downloaded and how big it is before the download starts."""
    found = local_checkpoint(stock, model)
    if found is not None:
        return found
    from huggingface_hub import snapshot_download

    repo, patterns, sub = _source(stock, model)
    size = download_size(repo, patterns)
    what = f"{repo}" + (f" ({sub})" if sub else f" ({stock})" if not model else "")
    how_big = f"{size / 1e6:,.0f} MB" if size else "size unknown"
    message = (
        f"Downloading the Laya checkpoint {what}: {how_big}, once, into the Hugging Face cache"
    )
    log.warning(message)
    if announce is not None:
        announce(message)
    root = Path(snapshot_download(repo, allow_patterns=patterns))
    return root / sub if sub else root


# Temperature buckets (question type : option count) Tempo asks. Choices have at most 10
# options (laya_decider.MAX_SHORTLIST; task type has 8), so the "11+" buckets are never used.
UNUSED_BUCKET_SIZES = ("11+",)
_REJECTED = re.compile(r"([a-z]+:[0-9+\-]+)=([^\s,]+) -> ([0-9.eE+-]+)")


def explain_temperature_warning(message: str) -> str | None:
    """Laya warns when a checkpoint ships temperatures outside its own range. The stock
    English checkpoint ships choice:11+=0.10 (Laya's issue, not Tempo's): Tempo never asks a
    choice with 11 or more options, so it changes nothing here. Returns a plain note for that
    case, None when an entry Tempo does use is affected."""
    if "invalid temperatures" not in message:
        return None
    rejected = _REJECTED.findall(message)
    if not rejected or not all(b.split(":")[1] in UNUSED_BUCKET_SIZES for b, _, _ in rejected):
        return None
    shown = ", ".join(f"{b}={float(raw):.2f}" for b, raw, _ in rejected)
    return (
        f"Laya's checkpoint ships a temperature below Laya's own minimum ({shown}; Laya uses "
        "0.5). Tempo never asks a choice with 11 or more options, so this doesn't affect it."
    )


def _agent(make: Callable[[], Any]) -> Any:
    """Build a Laya agent, turning its known-harmless temperature warning into a log line."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent = make()
    for item in caught:
        note = explain_temperature_warning(str(item.message))
        if note:
            log.info(note)
        else:
            warnings.warn_explicit(
                item.message, item.category, item.filename, item.lineno, source=item.source
            )
    return agent


def machine_id() -> str:
    """What decides which runner is faster: the CPU and how many threads Laya uses."""
    return (
        f"{platform.system()}-{platform.machine()}-{platform.processor() or 'cpu'}-{os.cpu_count()}"
    )


def total_memory_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.length = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return status.total / 1e9
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (AttributeError, OSError, ValueError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def saved_runner(cache_dir: Path, checkpoint: str) -> dict[str, Any] | None:
    """The runner chosen on this machine for this checkpoint, if it was measured."""
    entry = _read_json(cache_dir / RUNNER_FILE).get(f"{checkpoint}|{machine_id()}")
    return entry if isinstance(entry, dict) and entry.get("backend") in BACKENDS else None


def save_runner(cache_dir: Path, checkpoint: str, entry: dict[str, Any]) -> None:
    path = cache_dir / RUNNER_FILE
    data = _read_json(path)
    data[f"{checkpoint}|{machine_id()}"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def save_status(cache_dir: Path, status: dict[str, Any]) -> None:
    """What `tempo-server doctor` shows about Laya: written by the server at each load."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / STATUS_FILE).write_text(json.dumps(status, indent=1), encoding="utf-8")


def read_status(cache_dir: Path) -> dict[str, Any]:
    return _read_json(cache_dir / STATUS_FILE)


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
        json.dumps({"checkpoint": str(model_dir), "int8": int8, "format": FORMAT_VERSION}),
        encoding="utf-8",
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
    announce: Callable[[str], None] | None = None,
) -> OnnxRunner | TorchRunner:
    """Load one Laya checkpoint for CPU inference with ``threads`` threads. The packages are
    checked first: nothing is downloaded unless the Laya extra is installed."""
    ensure_installed(backend)
    model_dir = checkpoint_dir(stock, model, announce)
    if backend == "torch":
        import torch
        from laya.agent import Agent

        torch.set_num_threads(threads)
        return TorchRunner(_agent(lambda: Agent(str(model_dir), device=device or "cpu")))
    import onnxruntime as ort
    from laya.onnx_agent import ONNXAgent

    path = onnx_file(model_dir, cache_dir, int8=backend == "onnx-int8")
    agent = _agent(lambda: ONNXAgent(str(model_dir), onnx_path=str(path)))
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
