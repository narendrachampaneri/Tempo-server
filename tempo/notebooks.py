"""The two Kaggle notebooks, kept here as data so there is one source for all of them:

- ``training/*.ipynb`` in the repository (``tempo-server train notebooks`` rewrites them; a test
  checks they match),
- the copies `tempo-server train prepare` writes next to the upload pack,
- the dry run, which executes their code cells on a CPU (``run``).

Each cell is short: the work is done by the training kit (tempo/trainkit.py), which travels in
the pack as ``tempo_trainkit.py``, so a notebook always runs the kit that matches its data.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BOOTSTRAP = """\
# Find the Tempo pack among the inputs (Kaggle unpacks the uploaded zip; a zip still works),
# then load the training kit that came with it.
import glob
import os
import sys
import zipfile
from pathlib import Path

INPUT = Path(os.environ.get("TEMPO_INPUT", "/kaggle/input"))
WORK = Path(os.environ.get("TEMPO_WORK", "/kaggle/working"))
WORK.mkdir(parents=True, exist_ok=True)
found = sorted(glob.glob(str(INPUT / "**" / "tempo-pack.json"), recursive=True))
if found:
    PACK = Path(found[-1]).parent
else:
    PACK = None
    for archive in sorted(glob.glob(str(INPUT / "**" / "*.zip"), recursive=True)):
        with zipfile.ZipFile(archive) as z:
            if "tempo-pack.json" in z.namelist():
                PACK = WORK / "pack"
                z.extractall(PACK)
                break
if PACK is None:
    raise SystemExit(
        "No Tempo training pack found. Right panel -> Add Input -> Datasets -> Your Work -> the "
        "dataset you uploaded (made by `tempo-server train prepare`)."
    )
sys.path.insert(0, str(PACK))
import tempo_trainkit as tk  # noqa: E402

print(f"Pack: {PACK} (training kit version {tk.KIT_VERSION})")
clock = tk.Clock()
"""

CORE: list[tuple[str, str]] = [
    (
        "markdown",
        """\
# Tempo-Core: fine-tune Qwen3-1.7B on Tempo's checked answers

LoRA SFT on `sft/` (answers that passed Tempo's checks), then DPO on `pairs/` (the answer that
passed against a draft that failed), then merge, convert to GGUF and quantize to **Q4_K_M** for
Ollama or llama.cpp on an ordinary CPU. The data comes from `tempo-server train prepare`; the
code is the training kit that came with it (`tempo_trainkit.py`).

**Before you run** (right panel): *Session options → Accelerator → GPU T4 x2* and *Internet →
On* (needs a phone-verified Kaggle account); *Add Input → Datasets → Your Work →* the dataset
you uploaded. Then **Save Version → Save & Run All (Commit)**: it runs in the background.

**Time** (estimates until measured; the report records the real times): about 1 hour of SFT
for 5,000 rows and 2 epochs, about 1 hour of DPO for 2,000 pairs, 10-15 minutes for the GGUF
step. Kaggle stops a session at 12 hours: the notebook saves a checkpoint 20 minutes before
its time budget and stops. To resume: *Add Input → Notebook Output →* the stopped version, and
run again.

**Output** (`/kaggle/working/tempo-core/`): `tempo-core-q4_k_m.gguf`, `Modelfile`,
`report.json`, `REPORT.md`. Download the output, then on your computer:
`tempo-server models import <zip>` → `tempo-server models compare` → `tempo-server models
promote`. See docs/TRAINING.md.""",
    ),
    (
        "code",
        """\
# Settings
import os

# Apache-2.0. The second family: "ibm-granite/granite-3.3-2b-instruct"
BASE_MODEL = "Qwen/Qwen3-1.7B"
TIME_BUDGET_HOURS = 11  # Kaggle stops a session at 12 hours; this leaves time to save
os.environ.setdefault("TEMPO_TIME_BUDGET_H", str(TIME_BUDGET_HOURS))""",
    ),
    ("markdown", "## 1. Load the pack and the training kit"),
    ("code", BOOTSTRAP),
    ("markdown", "## 2. GPU check and packages"),
    (
        "code",
        """\
hardware = tk.gpu_check(min_gpus=1)
tk.install(tk.CORE_PACKAGES)""",
    ),
    ("markdown", "## 3. Data, settings, and an earlier run to resume from"),
    (
        "code",
        """\
manifest = tk.load_pack(PACK)
tk.resume_from_inputs(INPUT, WORK)
state = tk.State(WORK)
cfg = tk.CoreConfig.for_run()
cfg.base = BASE_MODEL
print(cfg)""",
    ),
    ("markdown", "## 4. SFT (LoRA) on checked answers"),
    ("code", "sft_metrics = tk.sft(PACK, WORK, cfg, clock, state)"),
    ("markdown", "## 5. DPO on preference pairs (same LoRA)"),
    (
        "code",
        """\
dpo_metrics = tk.dpo(PACK, WORK, cfg, clock, state) if state.done("sft") else {}""",
    ),
    ("markdown", "## 6. Merge, convert to GGUF, quantize to Q4_K_M"),
    (
        "code",
        """\
files = tk.core_outputs(WORK, cfg, state) if state.done("dpo") else {}""",
    ),
    ("markdown", "## 7. Report"),
    (
        "code",
        """\
complete = state.done("gguf")
report = {
    "model": "Tempo-Core",
    "status": "complete" if complete else "incomplete",
    "run_id": tk.run_id(),
    "hardware": ", ".join(hardware["gpus"]) or hardware["cpu"],
    "base": cfg.base + (" (tiny random copy: dry run)" if cfg.tiny else ""),
    "base_licence": tk.CORE_BASES[cfg.base]["licence"],
    "base_source": tk.CORE_BASES[cfg.base]["source"],
    "ollama_base": tk.CORE_BASES[cfg.base]["ollama_base"],
    "dry_run": cfg.tiny,
    "settings": {k: v for k, v in vars(cfg).items()},
    "stages": {k: state.get(k) for k in ("sft", "dpo", "gguf") if state.get(k)},
    "heldout": {
        "sft_loss_before": state.get("sft").get("heldout_loss_before"),
        "sft_loss_after": state.get("sft").get("heldout_loss_after"),
        "dpo_reward_accuracy": state.get("dpo").get("heldout_reward_accuracy"),
        "dpo_reward_margin": state.get("dpo").get("heldout_reward_margin"),
    },
    "data": manifest,
    "files": files,
    "seconds": round(clock.elapsed()),
}
tk.write_report(WORK / "tempo-core", report)""",
    ),
]

ROUTER_JUDGE: list[tuple[str, str]] = [
    (
        "markdown",
        """\
# Tempo-Router and Tempo-Judge: fine-tune Laya on Tempo's decisions

Fine-tunes **Laya** (`convaiinnovations/laya`, Apache-2.0, 421M parameters) on the `laya/`
rows of the pack: Tempo's plan, pick and assess decisions (Tempo-Router) and its graded
answers (Tempo-Judge, the `quality` decisions), labelled by what happened. Based on Laya's
official notebook `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` (Apache-2.0):
the same preprocessing, loss (policy gradient with a proper scoring rule plus soft
cross-entropy), both T4 GPUs through `torchrun`, and temperature calibration on a held-out
slice. Added for Tempo: time budget, resume, and scores on Tempo's held-out questions before
and after.

**Before you run** (right panel): *Accelerator → GPU T4 x2*, *Internet → On*, *Add Input →*
your pack dataset. Then **Save Version → Save & Run All (Commit)**.

**Time** (estimate): 10-30 minutes for 5,000-20,000 rows (Laya's own fine-tune on 6,000
decisions took about 10 minutes on 2×T4).

**Output** (`/kaggle/working/tempo-router-judge/`): the checkpoint (`model.safetensors`,
`rl_agent_config.json`, `encoder/`, `tokenizer/`), `report.json`, `REPORT.md`. Then
`tempo-server models import <zip>` → `tempo-server models compare` → `tempo-server models
promote`.""",
    ),
    (
        "code",
        """\
# Settings
import os

EPOCHS = 4  # as Laya's official notebook
TIME_BUDGET_HOURS = 11
os.environ.setdefault("TEMPO_TIME_BUDGET_H", str(TIME_BUDGET_HOURS))""",
    ),
    ("markdown", "## 1. Load the pack and the training kit"),
    ("code", BOOTSTRAP),
    ("markdown", "## 2. GPU check and packages"),
    (
        "code",
        """\
hardware = tk.gpu_check(min_gpus=1)  # two T4s train in parallel (torchrun); one works too
tk.install(tk.LAYA_PACKAGES)""",
    ),
    ("markdown", "## 3. Data, base checkpoint, and an earlier run to resume from"),
    (
        "code",
        """\
manifest = tk.load_pack(PACK)
tk.resume_from_inputs(INPUT, WORK)
state = tk.State(WORK)
cfg = tk.LayaConfig.for_run()
if not cfg.tiny:
    cfg.epochs = EPOCHS
train_rows = tk.read_jsonl(PACK / "laya" / "train.jsonl")
test_rows = tk.read_jsonl(PACK / "laya" / "test.jsonl")
base_dir = tk.laya_base(WORK, cfg.tiny)
print(f"{len(train_rows)} training rows, {len(test_rows)} held-out rows; base {base_dir}")""",
    ),
    ("markdown", "## 4. Held-out score of the base checkpoint (before)"),
    (
        "code",
        """\
if not state.get("laya-before"):
    state.mark("laya-before", "done", **tk.laya_eval(base_dir, test_rows))
before = state.get("laya-before")
print(before)""",
    ),
    ("markdown", "## 5. Fine-tune (resumes from the last checkpoint)"),
    ("code", "laya_metrics = tk.run_laya_training(base_dir, WORK, train_rows, cfg, clock, state)"),
    ("markdown", "## 6. Held-out score of the fine-tuned checkpoint (after)"),
    (
        "code",
        """\
out = WORK / "tempo-router-judge"
after = tk.laya_eval(out, test_rows) if state.done("laya") else {}
print(after)""",
    ),
    ("markdown", "## 7. Report"),
    (
        "code",
        """\
files = {}
if state.done("laya"):
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.suffix in (".safetensors", ".json"):
            files[path.relative_to(out).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": tk.sha256(path),
            }
report = {
    "model": "Tempo-Router and Tempo-Judge (Laya)",
    "status": "complete" if state.done("laya") else "incomplete",
    "run_id": tk.run_id(),
    "hardware": ", ".join(hardware["gpus"]) or hardware["cpu"],
    "base": tk.LAYA_BASE["repo"] + (" (tiny random copy: dry run)" if cfg.tiny else ""),
    "base_licence": tk.LAYA_BASE["licence"],
    "base_source": tk.LAYA_BASE["source"],
    "dry_run": cfg.tiny,
    "settings": {k: v for k, v in vars(cfg).items() if k not in ("started", "budget_s")},
    "stages": {"laya": state.get("laya")},
    "heldout": {
        "accuracy_before": before.get("accuracy"),
        "accuracy_after": after.get("accuracy"),
        "per_decision_before": before.get("decisions"),
        "per_decision_after": after.get("decisions"),
    },
    "data": manifest,
    "files": files,
    "seconds": round(clock.elapsed()),
}
tk.write_report(out, report)""",
    ),
]

NOTEBOOKS = {"tempo_core.ipynb": CORE, "tempo_router_judge.ipynb": ROUTER_JUDGE}


def notebook(cells: list[tuple[str, str]]) -> dict[str, Any]:
    out = []
    for kind, text in cells:
        lines = text.rstrip("\n").splitlines(keepends=True)
        cell: dict[str, Any] = {"cell_type": kind, "metadata": {}, "source": lines}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        out.append(cell)
    return {
        "cells": out,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def render(name: str) -> str:
    return json.dumps(notebook(NOTEBOOKS[name]), indent=1, ensure_ascii=False) + "\n"


def write_all(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in NOTEBOOKS:
        path = folder / name
        path.write_text(render(name), encoding="utf-8")
        paths.append(path)
    return paths


@contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(name: str, inputs: Path, work: Path, say: Any = print) -> dict[str, Any]:
    """Execute a notebook's code cells in order, in one namespace, as Kaggle would (dry run:
    ``TEMPO_DRY_RUN=1``, CPU, tiny models). Returns the namespace."""
    import sys

    namespace: dict[str, Any] = {"__name__": "__main__"}
    env = {"TEMPO_DRY_RUN": "1", "TEMPO_INPUT": str(inputs), "TEMPO_WORK": str(work)}
    code = [text for kind, text in NOTEBOOKS[name] if kind == "code"]
    titles = [text for kind, text in NOTEBOOKS[name] if kind == "markdown"][1:]
    with _env(env):
        try:
            for index, source in enumerate(code):
                title = titles[index - 1].lstrip("# ") if 0 < index <= len(titles) else "Settings"
                say(f"[{name}] {title}")
                started = time.time()
                exec(compile(source, f"{name}:cell{index + 1}", "exec"), namespace)  # noqa: S102
                say(f"[{name}] ... {time.time() - started:.0f} s")
        finally:
            sys.modules.pop("tempo_trainkit", None)
            pack = namespace.get("PACK")
            if pack is not None and str(pack) in sys.path:
                sys.path.remove(str(pack))
    return namespace
