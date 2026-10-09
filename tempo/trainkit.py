"""Tempo's training kit: the code the Kaggle notebooks run (training/*.ipynb).

`tempo-server train prepare` copies this file into the upload pack as ``tempo_trainkit.py``, so
a notebook always runs the kit that matches its data. It is standalone (it never imports
``tempo``) and needs the training libraries (``pip install "tempo-server[train]"``, which the
notebooks install on Kaggle). `tempo-server train dry-run` runs the same code on a CPU with tiny
models.

- **Tempo-Core**: LoRA SFT on ``sft/`` (TRL's SFTTrainer), DPO on ``pairs/`` (TRL's
  DPOTrainer, same LoRA), merge, convert to GGUF with llama.cpp and quantize to Q4_K_M.
- **Tempo-Router and Tempo-Judge**: fine-tune Laya on ``laya/``, following Laya's official
  notebook (``notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb``, Apache-2.0): the
  same preprocessing, policy-gradient + soft cross-entropy loss, DDP on two GPUs, and
  temperature calibration on a held-out slice.

Every stage prints its progress (step, elapsed, time left), saves checkpoints, stops cleanly
before Kaggle's session limit, and resumes from the last checkpoint on the next run. Each
notebook ends with ``report.json`` and ``REPORT.md``.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

KIT_VERSION = 1
PACK_MANIFEST = "tempo-pack.json"

# Base models (licences from each model's Hugging Face page, checked 2026-09-28; see
# docs/TEMPO_MODELS.md). Rule 2: only Apache-2.0 or MIT bases.
CORE_BASES = {
    "Qwen/Qwen3-1.7B": {
        "licence": "Apache-2.0",
        "source": "https://huggingface.co/Qwen/Qwen3-1.7B",
        "checked": "2026-09-28",
        "ollama_base": "qwen3:1.7b",
    },
    "ibm-granite/granite-3.3-2b-instruct": {
        "licence": "Apache-2.0",
        "source": "https://huggingface.co/ibm-granite/granite-3.3-2b-instruct",
        "checked": "2026-09-28",
        "ollama_base": "granite3.3:2b",
    },
}
LAYA_BASE = {
    "repo": "convaiinnovations/laya",
    "licence": "Apache-2.0",
    "source": "https://huggingface.co/convaiinnovations/laya",
    "checked": "2026-09-28",
}
# llama.cpp for the GGUF conversion (MIT). The release binaries are checked by SHA-256 and
# the converter's sparse checkout by its commit. Linux x86-64 only (Kaggle, CI).
LLAMA_CPP = {
    "tag": "b11249",
    "commit": "6d78fb0727fdd8fbae15b6b5e9e0c0951a750d69",
    "repo": "https://github.com/ggml-org/llama.cpp",
    "linux-x86_64": {
        "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11249/"
        "llama-b11249-bin-ubuntu-x64.tar.gz",
        "sha256": "d1aad0580a1f48cb55a9e8f7de0d6eed5e6415acfce65b677023f1fcd5c06083",
    },
}
# What the notebooks install on Kaggle (torch is already there, with CUDA).
CORE_PACKAGES = [
    "transformers>=5,<6",
    "peft>=0.17",
    "trl>=1.0,<1.15",  # the version the CPU dry run tests (pyproject.toml, `train` extra)
    "datasets>=3",
    "accelerate>=1.0",
    "sentencepiece",
    "gguf",
]
LAYA_PACKAGES = ["laya>=0.3.21", "transformers>=5,<6", "safetensors", "scipy"]
MODELFILE_TEMPLATE = """{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
<think>

</think>

"""


# --- environment ------------------------------------------------------------------------------


def say(text: str) -> None:
    print(text, flush=True)


def fmt_s(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"


def in_kaggle() -> bool:
    return bool(os.environ.get("KAGGLE_KERNEL_RUN_TYPE")) or Path("/kaggle/working").is_dir()


def dry_run() -> bool:
    return os.environ.get("TEMPO_DRY_RUN") == "1"


def folders() -> tuple[Path, Path]:
    """(input folder, working folder): Kaggle's, or the dry run's."""
    inputs = Path(os.environ.get("TEMPO_INPUT", "/kaggle/input"))
    work = Path(os.environ.get("TEMPO_WORK", "/kaggle/working"))
    work.mkdir(parents=True, exist_ok=True)
    return inputs, work


def gpu_check(min_gpus: int = 1) -> dict[str, Any]:
    """Print the GPUs. Without one, stop with the fix (a dry run continues on the CPU)."""
    import torch

    info: dict[str, Any] = {"cuda": torch.cuda.is_available(), "gpus": []}
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        info["gpus"].append(f"{p.name} ({p.total_memory / 1e9:.1f} GB)")
    info["bf16"] = bool(info["cuda"] and torch.cuda.is_bf16_supported())
    info["cpu"] = f"{platform.processor() or platform.machine()}, {os.cpu_count()} threads"
    say(f"GPUs: {', '.join(info['gpus']) or 'none'} · CPU: {info['cpu']}")
    if len(info["gpus"]) < min_gpus:
        if dry_run():
            say("No GPU: dry run on the CPU with tiny models (TEMPO_DRY_RUN=1).")
        else:
            raise SystemExit(
                "No GPU found. In Kaggle's right panel: Session options -> Accelerator -> "
                "GPU T4 x2 (Internet must be On too), then run again."
            )
    return info


def install(packages: list[str]) -> None:
    """pip-install what the notebook needs (skipped in a dry run: the [train] extra has it)."""
    if dry_run():
        say("Dry run: using the installed packages.")
        return
    started = time.time()
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *packages], check=True)
    say(f"Installed {len(packages)} packages in {fmt_s(time.time() - started)}.")


def find_pack(inputs: Path, work: Path) -> Path:
    """The folder with tempo-pack.json among the notebook's inputs (a zip is unpacked)."""
    found = sorted(glob.glob(str(inputs / "**" / PACK_MANIFEST), recursive=True))
    if found:
        return Path(found[-1]).parent
    for archive in sorted(glob.glob(str(inputs / "**" / "*.zip"), recursive=True)):
        with zipfile.ZipFile(archive) as z:
            if PACK_MANIFEST in z.namelist():
                target = work / "pack"
                z.extractall(target)
                return target
    raise SystemExit(
        f"No Tempo training pack under {inputs}. Add it: right panel -> Add Input -> Datasets "
        "-> Your Work -> the dataset you uploaded (made by `tempo-server train prepare`)."
    )


def load_pack(pack: Path) -> dict[str, Any]:
    manifest = json.loads((pack / PACK_MANIFEST).read_text(encoding="utf-8"))
    counts = manifest["counts"]
    say(
        f"Pack from Tempo-server {manifest['tempo_server']} ({manifest['created']}): "
        + ", ".join(f"{k} {v['train']} train / {v['test']} test" for k, v in counts.items())
    )
    mix = manifest["mix"]
    say(
        f"Data mix: {mix['public_share']:.0%} public or human (min {mix['min_public_share']:.0%}),"
        f" {mix['self_share']:.0%} from an earlier Tempo-Core (max {mix['max_self_share']:.0%})"
    )
    return manifest


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- time, checkpoints and resuming ---------------------------------------------------------


@dataclass
class Clock:
    """Wall-clock budget for one Kaggle session (12 hours; stop with time to save)."""

    budget_s: float = float(os.environ.get("TEMPO_TIME_BUDGET_H", "11")) * 3600
    started: float = field(default_factory=time.time)

    def elapsed(self) -> float:
        return time.time() - self.started

    def left(self) -> float:
        return self.budget_s - self.elapsed()


class State:
    """Which stages are done, kept in ``checkpoints/state.json`` (part of the notebook's
    output, so the next run can resume)."""

    def __init__(self, work: Path):
        self.dir = work / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.data: dict[str, Any] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )

    def done(self, stage: str) -> bool:
        return self.data.get(stage, {}).get("status") == "done"

    def get(self, stage: str) -> dict[str, Any]:
        return self.data.get(stage, {})

    def mark(self, stage: str, status: str, **info: Any) -> None:
        self.data[stage] = {**self.data.get(stage, {}), "status": status, **info}
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def resume_from_inputs(inputs: Path, work: Path) -> bool:
    """Copy the checkpoints of an earlier run (added as input: Add Input -> Notebook Output)
    into the working folder. True when something was restored."""
    if (work / "checkpoints" / "state.json").exists():
        return True  # same session, or already restored
    found = sorted(glob.glob(str(inputs / "**" / "checkpoints" / "state.json"), recursive=True))
    if not found:
        say("No earlier run among the inputs: starting fresh.")
        return False
    source = Path(found[-1]).parent
    shutil.copytree(source, work / "checkpoints", dirs_exist_ok=True)
    state = json.loads((source / "state.json").read_text(encoding="utf-8"))
    say(f"Resuming from {source}: " + ", ".join(f"{k} {v['status']}" for k, v in state.items()))
    return True


def latest_checkpoint(folder: Path) -> Path | None:
    points = [p for p in folder.glob("checkpoint-*") if p.name.split("-")[-1].isdigit()]
    return max(points, key=lambda p: int(p.name.split("-")[-1])) if points else None


def progress_callback(stage: str, clock: Clock, every_s: float = 60.0) -> Any:
    """A Trainer callback: a progress line every ``every_s`` seconds, and a clean stop (with a
    checkpoint) when the session's time is nearly up."""
    from transformers import TrainerCallback

    class Progress(TrainerCallback):
        def __init__(self) -> None:
            self.last = 0.0
            self.stage_start = time.time()
            self.first_step: int | None = None
            self.stopped_for_time = False
            self.loss: float | None = None

        def on_log(self, args, state, control, logs=None, **kwargs):  # noqa: ANN001
            if logs and "loss" in logs:
                self.loss = float(logs["loss"])

        def on_step_end(self, args, state, control, **kwargs):  # noqa: ANN001
            if self.first_step is None:
                self.first_step = state.global_step - 1
            now = time.time()
            done = state.global_step - self.first_step
            total = state.max_steps
            if now - self.last >= every_s or state.global_step == total:
                self.last = now
                rate = (now - self.stage_start) / max(done, 1)
                eta = rate * (total - state.global_step)
                loss = f" · loss {self.loss:.4f}" if self.loss is not None else ""
                say(
                    f"  {stage}: step {state.global_step}/{total} "
                    f"({state.global_step / max(total, 1):.0%}) · {fmt_s(now - self.stage_start)}"
                    f" elapsed · about {fmt_s(eta)} left{loss} · session {fmt_s(clock.left())}"
                    " left"
                )
            if clock.left() < 20 * 60 and not self.stopped_for_time:
                say(f"  {stage}: 20 minutes of the session left: saving a checkpoint and stopping.")
                self.stopped_for_time = True
                control.should_save = True
                control.should_training_stop = True
            return control

    return Progress()


# --- Tempo-Core -----------------------------------------------------------------------------


@dataclass
class CoreConfig:
    base: str = "Qwen/Qwen3-1.7B"
    tiny: bool = False  # dry run: a tiny random model with the base's tokenizer
    epochs: float = 2.0
    max_steps: int = -1  # set in dry runs
    dpo_epochs: float = 1.0
    dpo_max_steps: int = -1
    learning_rate: float = 2e-4
    dpo_learning_rate: float = 5e-6
    dpo_beta: float = 0.1
    lora_rank: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    max_length: int = 1024
    batch_size: int = 4
    grad_accum: int = 4
    save_steps: int = 200
    quant: str = "Q4_K_M"
    base_gguf: bool = False  # also convert the untuned base (the dry run compares against it)
    keep_merged: bool = False

    @classmethod
    def for_run(cls) -> CoreConfig:
        if dry_run():
            return cls(
                tiny=True,
                max_steps=100,
                dpo_max_steps=20,
                learning_rate=2e-4,
                dpo_learning_rate=5e-5,
                max_length=256,
                batch_size=4,
                grad_accum=1,
                save_steps=50,
                base_gguf=True,
            )
        return cls()


def tiny_base(base: str, out: Path) -> Path:
    """A tiny random model with ``base``'s architecture and tokenizer (about 40M parameters),
    for dry runs: it proves every step works, not that anything was learned."""
    if (out / "config.json").exists():
        return out
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    source = snapshot_download(
        base,
        allow_patterns=["config.json", "generation_config.json", "tokenizer*", "*.jinja"],
    )
    config = AutoConfig.from_pretrained(source)
    config.update(
        {
            "hidden_size": 256,
            "intermediate_size": 512,
            "num_hidden_layers": 2,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
            "head_dim": 64,
        }
    )
    if getattr(config, "layer_types", None):
        config.layer_types = config.layer_types[:2]
    torch.manual_seed(0)
    model = AutoModelForCausalLM.from_config(config, dtype=torch.float32)
    model.save_pretrained(out)
    AutoTokenizer.from_pretrained(source).save_pretrained(out)
    say(f"Tiny {base} for the dry run: {sum(p.numel() for p in model.parameters()) / 1e6:.0f}M")
    return out


def _precision() -> dict[str, Any]:
    """bf16 where the GPU has it; T4 and P100 train fp16 on fp32 weights; CPU fp32."""
    import torch

    if torch.cuda.is_available():
        if torch.cuda.is_bf16_supported():
            return {"dtype": torch.bfloat16, "bf16": True, "fp16": False}
        return {"dtype": torch.float32, "bf16": False, "fp16": True}
    return {"dtype": torch.float32, "bf16": False, "fp16": False, "use_cpu": True}


def _quiet_datasets() -> None:
    from datasets.utils.logging import disable_progress_bar

    disable_progress_bar()


def _quiet(trainer: Any) -> Any:
    """Our progress lines replace the Trainer's raw log dictionaries and progress bars."""
    from transformers.trainer_callback import PrinterCallback, ProgressCallback

    trainer.remove_callback(PrinterCallback)
    trainer.remove_callback(ProgressCallback)
    return trainer


def _conversations(rows: list[dict[str, Any]]) -> Any:
    """SFT rows as prompt/completion conversations, so the loss is on the answer only."""
    from datasets import Dataset

    return Dataset.from_list(
        [{"prompt": r["messages"][:-1], "completion": r["messages"][-1:]} for r in rows]
    )


def _pairs(rows: list[dict[str, Any]]) -> Any:
    from datasets import Dataset

    return Dataset.from_list(
        [{"prompt": r["prompt"], "chosen": r["chosen"], "rejected": r["rejected"]} for r in rows]
    )


def core_base(cfg: CoreConfig, work: Path) -> str:
    if cfg.base not in CORE_BASES:
        raise SystemExit(f"{cfg.base} is not an allowed base (Apache-2.0/MIT): {list(CORE_BASES)}")
    return str(tiny_base(cfg.base, work / "tiny-base")) if cfg.tiny else cfg.base


def _train_args(kind: str, cfg: CoreConfig, out: Path, **extra: Any) -> dict[str, Any]:
    precision = {k: v for k, v in _precision().items() if k != "dtype"}
    return {
        "output_dir": str(out),
        "per_device_train_batch_size": cfg.batch_size,
        "per_device_eval_batch_size": cfg.batch_size,
        "gradient_accumulation_steps": cfg.grad_accum,
        "save_strategy": "steps",
        "save_steps": cfg.save_steps,
        "save_total_limit": 2,
        "logging_steps": 10,
        "report_to": [],
        "disable_tqdm": True,
        "gradient_checkpointing": not cfg.tiny,
        "max_length": cfg.max_length,
        "seed": 17,
        **precision,
        **extra,
    }


def sft(pack: Path, work: Path, cfg: CoreConfig, clock: Clock, state: State) -> dict[str, Any]:
    """LoRA SFT on the pack's checked answers. Resumes from its last checkpoint."""
    if state.done("sft"):
        say("SFT: already done (resumed run).")
        return state.get("sft")
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    started = time.time()
    base = core_base(cfg, work)
    train = read_jsonl(pack / "sft" / "train.jsonl")
    test = read_jsonl(pack / "sft" / "test.jsonl")
    if not train:
        raise SystemExit("The pack has no SFT rows: nothing to train Tempo-Core on.")
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=_precision()["dtype"])
    out = state.dir / "sft"
    args = SFTConfig(
        **_train_args(
            "sft",
            cfg,
            out,
            num_train_epochs=cfg.epochs,
            max_steps=cfg.max_steps,
            learning_rate=cfg.learning_rate,
            lr_scheduler_type="cosine",
            warmup_steps=0.03,  # a float below 1 is a share of the steps
        )
    )
    lora = LoraConfig(
        r=cfg.lora_rank,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        target_modules="all-linear",
        task_type="CAUSAL_LM",
    )
    progress = progress_callback("SFT", clock)
    _quiet_datasets()
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=_conversations(train),
        eval_dataset=_conversations(test) if test else None,
        processing_class=tok,
        peft_config=lora,
        callbacks=[progress],
    )
    _quiet(trainer)
    before = trainer.evaluate()["eval_loss"] if test else None
    if before is not None:
        say(f"SFT: held-out loss before training {before:.4f}")
    resume = latest_checkpoint(out)
    if resume:
        say(f"SFT: resuming from {resume.name}")
    say(f"SFT: {len(train)} rows, base {cfg.base}{' (tiny)' if cfg.tiny else ''}")
    result = trainer.train(resume_from_checkpoint=str(resume) if resume else None)
    if progress.stopped_for_time:
        state.mark("sft", "incomplete", reason="session time limit")
        return state.get("sft")
    after = trainer.evaluate()["eval_loss"] if test else None
    trainer.save_model(str(state.dir / "sft-adapter"))
    tok.save_pretrained(str(state.dir / "sft-adapter"))
    metrics = {
        "rows": len(train),
        "steps": result.global_step,
        "train_loss": round(float(result.training_loss), 4),
        "heldout_loss_before": round(float(before), 4) if before is not None else None,
        "heldout_loss_after": round(float(after), 4) if after is not None else None,
        "seconds": round(time.time() - started + state.get("sft").get("seconds", 0)),
    }
    state.mark("sft", "done", **metrics)
    shutil.rmtree(out, ignore_errors=True)  # the adapter is saved; the checkpoints are not needed
    say(f"SFT done: {json.dumps(metrics)}")
    del trainer, model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return state.get("sft")


def dpo(pack: Path, work: Path, cfg: CoreConfig, clock: Clock, state: State) -> dict[str, Any]:
    """DPO on the pack's preference pairs, continuing the SFT LoRA. Skipped without pairs."""
    if state.done("dpo"):
        say("DPO: already done (resumed run).")
        return state.get("dpo")
    if not state.done("sft"):
        raise SystemExit("DPO needs SFT to finish first.")
    train = read_jsonl(pack / "pairs" / "train.jsonl")
    test = read_jsonl(pack / "pairs" / "test.jsonl")
    if not train:
        state.mark("dpo", "done", skipped="no preference pairs in the pack")
        say("DPO: skipped (no preference pairs).")
        return state.get("dpo")
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    started = time.time()
    base = core_base(cfg, work)
    tok = AutoTokenizer.from_pretrained(state.dir / "sft-adapter")
    model = AutoModelForCausalLM.from_pretrained(base, dtype=_precision()["dtype"])
    model = PeftModel.from_pretrained(model, state.dir / "sft-adapter", is_trainable=True)
    out = state.dir / "dpo"
    args = DPOConfig(
        **_train_args(
            "dpo",
            cfg,
            out,
            num_train_epochs=cfg.dpo_epochs,
            max_steps=cfg.dpo_max_steps,
            learning_rate=cfg.dpo_learning_rate,
            beta=cfg.dpo_beta,
        )
    )
    progress = progress_callback("DPO", clock)
    _quiet_datasets()
    trainer = DPOTrainer(
        model=model,
        args=args,
        train_dataset=_pairs(train),
        eval_dataset=_pairs(test) if test else None,
        processing_class=tok,
        callbacks=[progress],
    )
    _quiet(trainer)
    resume = latest_checkpoint(out)
    if resume:
        say(f"DPO: resuming from {resume.name}")
    say(f"DPO: {len(train)} pairs")
    result = trainer.train(resume_from_checkpoint=str(resume) if resume else None)
    if progress.stopped_for_time:
        state.mark("dpo", "incomplete", reason="session time limit")
        return state.get("dpo")
    scores = trainer.evaluate() if test else {}
    trainer.save_model(str(state.dir / "dpo-adapter"))
    tok.save_pretrained(str(state.dir / "dpo-adapter"))

    def num(key: str) -> float | None:
        return round(float(scores[key]), 4) if key in scores else None

    metrics = {
        "pairs": len(train),
        "steps": result.global_step,
        "train_loss": round(float(result.training_loss), 4),
        "heldout_reward_accuracy": num("eval_rewards/accuracies"),
        "heldout_reward_margin": num("eval_rewards/margins"),
        "heldout_loss": num("eval_loss"),
        "seconds": round(time.time() - started),
    }
    state.mark("dpo", "done", **metrics)
    shutil.rmtree(out, ignore_errors=True)
    say(f"DPO done: {json.dumps(metrics)}")
    del trainer, model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return state.get("dpo")


def merge(work: Path, cfg: CoreConfig, state: State) -> Path:
    """Merge the last adapter (DPO, else SFT) into the base, saved in bf16 on the CPU."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    merged = work / "merged"
    if (merged / "config.json").exists():
        return merged
    adapter = state.dir / "dpo-adapter"
    if not (adapter / "adapter_config.json").exists():
        adapter = state.dir / "sft-adapter"
    started = time.time()
    model = AutoModelForCausalLM.from_pretrained(core_base(cfg, work), dtype=torch.float32)
    model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
    model.to(torch.bfloat16).save_pretrained(merged)
    AutoTokenizer.from_pretrained(adapter).save_pretrained(merged)
    say(f"Merged {adapter.name} into the base in {fmt_s(time.time() - started)}.")
    return merged


def llama_cpp(work: Path) -> dict[str, Path]:
    """llama.cpp's converter (sparse checkout at a pinned commit) and its release binaries
    (llama-quantize, llama-server; SHA-256 checked). Cached in ``<work>/llama.cpp``."""
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise SystemExit("The GGUF step runs on Linux x86-64 (Kaggle or CI).")
    home = Path(os.environ.get("TEMPO_LLAMA_CPP", work / "llama.cpp"))
    source, binaries = home / "src", home / f"llama-{LLAMA_CPP['tag']}"
    if not (source / "convert_hf_to_gguf.py").exists():
        shutil.rmtree(source, ignore_errors=True)
        git = ["git", "-c", "advice.detachedHead=false"]
        subprocess.run(
            [*git, "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP["tag"]]
            + ["--filter=blob:none", "--sparse", LLAMA_CPP["repo"], str(source)],
            check=True,
        )
        subprocess.run(
            [*git, "-C", str(source), "sparse-checkout", "set", "--no-cone"]
            + ["/gguf-py/", "/conversion/", "/convert_hf_to_gguf.py", "/convert_lora_to_gguf.py"],
            check=True,
        )
        head = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if head != LLAMA_CPP["commit"]:
            shutil.rmtree(source)
            raise SystemExit(f"llama.cpp {LLAMA_CPP['tag']} is at {head}, not the pinned commit")
    if not (binaries / "llama-quantize").exists():
        import urllib.request

        asset = LLAMA_CPP["linux-x86_64"]
        archive = home / "llama-bin.tar.gz"
        home.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(asset["url"], archive)  # noqa: S310 (pinned https URL)
        if sha256(archive) != asset["sha256"]:
            archive.unlink()
            raise SystemExit("llama.cpp binaries: SHA-256 mismatch; download refused")
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if member.name.startswith(("/", "..")) or ".." in Path(member.name).parts:
                    raise SystemExit(f"unsafe path in the llama.cpp archive: {member.name}")
            tar.extractall(home, filter="data")
        archive.unlink()
    return {
        "convert": source / "convert_hf_to_gguf.py",
        "quantize": binaries / "llama-quantize",
        "server": binaries / "llama-server",
    }


def to_gguf(model_dir: Path, out: Path, tools: dict[str, Path], quant: str = "Q4_K_M") -> Path:
    """HF folder -> bf16 GGUF -> ``quant`` GGUF (the bf16 file is removed)."""
    if out.exists():
        return out
    started = time.time()
    full = out.with_suffix(".bf16.gguf")
    for command in (
        [sys.executable, str(tools["convert"]), str(model_dir), "--outfile", str(full)]
        + ["--outtype", "bf16"],
        [str(tools["quantize"]), str(full), str(out), quant],
    ):
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode:
            print(done.stdout[-4000:], done.stderr[-4000:])
            raise SystemExit(
                f"{Path(command[1 if command[0] == sys.executable else 0]).name} "
                f"failed (exit {done.returncode})"
            )
    full.unlink()
    say(
        f"GGUF {quant}: {out.name}, {out.stat().st_size / 1e6:.0f} MB, "
        f"{fmt_s(time.time() - started)}"
    )
    return out


def modelfile(gguf_name: str) -> str:
    """Ollama Modelfile: ChatML without thinking, as Tempo-Core was trained."""
    return (
        f"FROM ./{gguf_name}\n"
        f'TEMPLATE """{MODELFILE_TEMPLATE}"""\n'
        'PARAMETER stop "<|im_end|>"\n'
        "PARAMETER temperature 0.7\n"
        "PARAMETER top_p 0.8\n"
        "PARAMETER top_k 20\n"
    )


def core_outputs(work: Path, cfg: CoreConfig, state: State) -> dict[str, Any]:
    """Merge, convert and quantize; write the Modelfile. Returns the files made."""
    out = work / "tempo-core"
    out.mkdir(exist_ok=True)
    tools = llama_cpp(work)
    name = f"tempo-core-{cfg.quant.lower()}.gguf"
    gguf = to_gguf(merge(work, cfg, state), out / name, tools, cfg.quant)
    (out / "Modelfile").write_text(modelfile(name), encoding="utf-8")
    files = {name: {"bytes": gguf.stat().st_size, "sha256": sha256(gguf)}}
    if cfg.base_gguf:
        base = to_gguf(Path(core_base(cfg, work)), out / "base.gguf", tools, cfg.quant)
        files["base.gguf"] = {"bytes": base.stat().st_size, "sha256": sha256(base)}
    if not cfg.keep_merged:
        shutil.rmtree(work / "merged", ignore_errors=True)
    state.mark("gguf", "done", files=files)
    return files


# --- Tempo-Router and Tempo-Judge (Laya) ----------------------------------------------------


@dataclass
class LayaConfig:
    tiny: bool = False
    epochs: int = 4
    micro_batch: int = 8
    grad_accum: int = 4
    group_size: int = 4
    lr_encoder: float = 2.5e-5
    lr_head: float = 1.0e-4
    sigma_start: float = 0.4
    sigma_end: float = 0.1
    calib_max: int = 400
    max_len: int = 1024
    head_max_len: int = 256
    budget_s: float = 11 * 3600
    started: float = 0.0

    @classmethod
    def for_run(cls) -> LayaConfig:
        if dry_run():
            return cls(tiny=True, epochs=2, micro_batch=4, grad_accum=1, max_len=512)
        return cls()


def laya_base(work: Path, tiny: bool) -> Path:
    """Laya's stock checkpoint, or (dry run) a tiny random one with Laya's tokenizer."""
    from huggingface_hub import snapshot_download
    from laya.agent import _fix_tokenizer_config

    patterns = ["rl_agent_config.json", "tokenizer/*", "encoder/*"]
    if not tiny:
        path = snapshot_download(LAYA_BASE["repo"], allow_patterns=[*patterns, "model.safetensors"])
        _fix_tokenizer_config(path)
        return Path(path)
    out = work / "tiny-laya"
    if (out / "model.safetensors").exists():
        return out
    import torch
    from laya.common import DecisionModel, _apply_rope_config
    from safetensors.torch import save_file
    from transformers import AutoConfig, AutoModel

    source = Path(snapshot_download(LAYA_BASE["repo"], allow_patterns=patterns))
    shutil.copytree(source / "tokenizer", out / "tokenizer", dirs_exist_ok=True)
    (out / "encoder").mkdir(parents=True, exist_ok=True)
    encoder = json.loads((source / "encoder" / "config.json").read_text(encoding="utf-8"))
    encoder.update(hidden_size=128, intermediate_size=256, num_hidden_layers=3)
    encoder.update(num_attention_heads=4, layer_types=encoder["layer_types"][:3])
    (out / "encoder" / "config.json").write_text(json.dumps(encoder, indent=1), encoding="utf-8")
    cfg = json.loads((source / "rl_agent_config.json").read_text(encoding="utf-8"))
    cfg.pop("temperature_by_options", None)
    cfg["temperature"] = [1.0, 1.0, 1.0]
    (out / "rl_agent_config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    _fix_tokenizer_config(str(out))
    torch.manual_seed(0)
    ecfg = AutoConfig.from_pretrained(out / "encoder")
    _apply_rope_config(ecfg)
    model = DecisionModel(
        AutoModel.from_config(ecfg, attn_implementation="sdpa"),
        cfg.get("head_layers", 2),
        len(cfg.get("act_costs", {})) + 1,
    )
    save_file({k: v.contiguous() for k, v in model.state_dict().items()}, out / "model.safetensors")
    say(f"Tiny Laya for the dry run: {sum(p.numel() for p in model.parameters()) / 1e6:.0f}M")
    return out


def laya_items(rows: list[dict[str, Any]], model_dir: Path) -> list[dict[str, Any]]:
    """Tokenized training items, exactly as Laya's official notebook builds them."""
    from laya.common import QTYPES, build_sequence, render_options
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    cfg = json.loads((model_dir / "rl_agent_config.json").read_text(encoding="utf-8"))
    items = []
    for row in rows:
        state = json.loads(row["state"])
        questions = json.loads(row["questions"])
        gold = json.loads(row["gold"])
        for qid, q in questions.items():
            if qid not in gold:
                continue
            t, crit, probs = q["type"], q.get("criteria", {}), gold[qid]["probabilities"]
            if t == "choice":
                target = [probs.get(k, 0.0) for k in crit]
            elif t == "noul":
                target = [probs.get("false", 0.5), probs.get("true", 0.5)]
            else:
                levels = len(crit) if isinstance(crit, list) else 4
                target = [probs.get(str(i), 0.0) for i in range(levels)]
            total = sum(target)
            target = [v / total for v in target] if total > 0 else [1 / len(target)] * len(target)
            spec = {"t": t, "ins": q["instructions"], "crit": crit}
            seq, markers = build_sequence(tok, state, spec, cfg["max_len"], cfg["head_max_len"])
            if len(markers) != len(render_options({"t": t, "crit": crit})):
                continue
            items.append(
                {
                    "ids": seq,
                    "markers": markers,
                    "qtype": QTYPES[t],
                    "target": target,
                    "label": target.index(max(target)),
                }
            )
    return items


def _collate(items: list[dict[str, Any]], pad_id: int) -> dict[str, Any]:
    import torch

    n, length = len(items), max(len(it["ids"]) for it in items)
    kmax = max(len(it["markers"]) for it in items)
    ids = torch.full((n, length), pad_id, dtype=torch.long)
    att = torch.zeros((n, length), dtype=torch.long)
    mpos = torch.zeros((n, kmax), dtype=torch.long)
    mmask = torch.zeros((n, kmax), dtype=torch.bool)
    target = torch.zeros((n, kmax), dtype=torch.float32)
    for i, it in enumerate(items):
        ids[i, : len(it["ids"])] = torch.tensor(it["ids"])
        att[i, : len(it["ids"])] = 1
        k = len(it["markers"])
        mpos[i, :k] = torch.tensor(it["markers"])
        mmask[i, :k] = True
        target[i, : len(it["target"])] = torch.tensor(it["target"], dtype=torch.float32)
    return {
        "input_ids": ids,
        "attention_mask": att,
        "marker_pos": mpos,
        "marker_mask": mmask,
        "target": target,
        "qtype": torch.tensor([it["qtype"] for it in items]),
    }


# Laya refuses temperatures outside this range and warns ("this checkpoint ships invalid
# temperatures or values outside [0.5, 5]"): below 1 a temperature sharpens the logits, and
# far below it misstates confidence. laya/common.py TEMP_MIN / TEMP_MAX (checked 2026-09-29:
# https://github.com/NandhaKishorM/laya/blob/main/laya/common.py).
LAYA_TEMP_MIN, LAYA_TEMP_MAX = 0.5, 5.0


def laya_temperature(value: float) -> float:
    """A fitted temperature Laya will accept as it is."""
    return min(LAYA_TEMP_MAX, max(LAYA_TEMP_MIN, float(value)))


def _fit_temperature(selected: list[tuple[Any, list[float]]]) -> float:
    import torch

    if len(selected) < 10:
        return 1.0
    kmax = max(len(z) for z, _ in selected)
    logits = torch.full((len(selected), kmax), -1e4)
    targets = torch.zeros((len(selected), kmax))
    for i, (z, t) in enumerate(selected):
        logits[i, : len(z)] = torch.tensor(z)
        targets[i, : len(t)] = torch.tensor(t, dtype=torch.float32)
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():  # noqa: ANN202
        opt.zero_grad()
        loss = -(targets * torch.log_softmax(logits / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    # Within Laya's own range, so the checkpoint loads without "invalid temperatures".
    return laya_temperature(log_t.exp().item())


def laya_train(model_dir: Path, out: Path, items_path: Path, cfg: LayaConfig) -> dict[str, Any]:
    """Laya's official fine-tune (RLCD policy gradient + soft cross-entropy, temperature
    calibration), on one device or under ``torchrun`` (DDP). A rolling checkpoint with the
    optimizer is saved every epoch and when the session's time is nearly up; the next run
    resumes from it."""
    import torch
    import torch.distributed as dist
    from laya.common import build_model, proper_reward
    from safetensors.torch import load_file, save_file
    from transformers import AutoTokenizer

    world = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    cuda = torch.cuda.is_available()
    if world > 1:
        dist.init_process_group("nccl")
        local = int(os.environ.get("LOCAL_RANK", "0"))
        torch.cuda.set_device(local)
        device = torch.device("cuda", local)
    else:
        device = torch.device("cuda" if cuda else "cpu")
    base_cfg = json.loads((model_dir / "rl_agent_config.json").read_text(encoding="utf-8"))
    run_cfg = {**base_cfg, "gradient_checkpointing": cuda, "max_tokens_per_batch": 4096}
    run_cfg.update(max_len=cfg.max_len, head_max_len=cfg.head_max_len)
    tok = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    model = build_model(run_cfg, encoder_dir=str(model_dir / "encoder"))
    rolling = out / "checkpoint_latest"
    resume = rolling / "trainer_state.pt"
    weights = rolling / "model.safetensors" if resume.exists() else model_dir / "model.safetensors"
    model.load_state_dict({k: v.float() for k, v in load_file(weights).items()}, strict=True)
    if cuda:
        model.encoder.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )
        model.head_checkpointing = True
    model.to(device).train()
    net = model
    if world > 1:
        from torch.nn.parallel import DistributedDataParallel

        net = DistributedDataParallel(model, device_ids=[device.index], find_unused_parameters=True)

    all_items = torch.load(items_path, weights_only=False)
    # The calibration slice is held out of training (same fixed seed on every rank).
    order = list(range(len(all_items)))
    random.Random(20260922).shuffle(order)
    n_calib = min(cfg.calib_max, len(all_items) // 10)
    calib = [all_items[i] for i in sorted(order[:n_calib])]
    train = [all_items[i] for i in sorted(order[n_calib:])]
    mine = train[rank::world]
    enc = [p for n, p in net.named_parameters() if "encoder." in n]
    head = [p for n, p in net.named_parameters() if "encoder." not in n]
    optimizer = torch.optim.AdamW(
        [{"params": enc, "lr": cfg.lr_encoder}, {"params": head, "lr": cfg.lr_head}],
        weight_decay=0.01,
    )
    updates = max(1, math.ceil(len(mine) / (cfg.micro_batch * cfg.grad_accum)) * cfg.epochs)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=updates, eta_min=1e-6)
    scaler = torch.amp.GradScaler("cuda", enabled=cuda)
    start_epoch, start_batch, history = 0, 0, []
    if resume.exists():
        saved = torch.load(resume, weights_only=False, map_location="cpu")
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        scaler.load_state_dict(saved["scaler"])
        start_epoch, start_batch, history = saved["epoch"], saved["batch"], saved["history"]
        if rank == 0:
            say(f"Laya: resuming at epoch {start_epoch + 1}, batch {start_batch}")

    def save_rolling(epoch: int, batch: int) -> None:
        if rank != 0:
            return
        rolling.mkdir(parents=True, exist_ok=True)
        tmp = rolling / "model.safetensors.tmp"
        save_file({k: v.contiguous().cpu() for k, v in model.state_dict().items()}, tmp)
        tmp.replace(rolling / "model.safetensors")
        torch.save(
            {
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "scaler": scaler.state_dict(),
                "epoch": epoch,
                "batch": batch,
                "history": history,
            },
            resume,
        )

    def time_is_up() -> bool:
        flag = torch.tensor(
            [float(time.time() - cfg.started > cfg.budget_s - 20 * 60)], device=device
        )
        if world > 1:
            dist.all_reduce(flag, op=dist.ReduceOp.MAX)
        return bool(flag.item())

    started = time.time()
    if rank == 0:
        say(
            f"Laya: {len(train)} training items ({len(calib)} held out for calibration), "
            f"{world} device(s) ({device.type}), {cfg.epochs} epochs"
        )
    stopped = False
    for epoch in range(start_epoch, cfg.epochs):
        rng = random.Random(42 + epoch + rank)
        epoch_items = list(mine)
        rng.shuffle(epoch_items)
        sigma = cfg.sigma_start + (cfg.sigma_end - cfg.sigma_start) * (
            epoch / max(1, cfg.epochs - 1)
        )
        total_loss, batches = 0.0, 0
        optimizer.zero_grad(set_to_none=True)
        first = start_batch if epoch == start_epoch else 0
        last_print = time.time()
        n_batches = math.ceil(len(epoch_items) / cfg.micro_batch)
        for index in range(first, n_batches):
            chunk = epoch_items[index * cfg.micro_batch : (index + 1) * cfg.micro_batch]
            batch = {k: v.to(device) for k, v in _collate(chunk, tok.pad_token_id).items()}
            with torch.autocast("cuda", dtype=torch.float16, enabled=cuda):
                logits, act = net(
                    batch["input_ids"],
                    batch["attention_mask"],
                    batch["marker_pos"],
                    batch["marker_mask"],
                    batch["qtype"],
                )
            logits = logits.float()
            mask = batch["marker_mask"]
            k = mask.sum(-1, keepdim=True).float()
            target = batch["target"]
            eps = torch.randn((cfg.group_size, *logits.shape), device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            z = logits.detach().unsqueeze(0) + eps
            q = torch.softmax(z.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                reward = proper_reward(
                    q, target.unsqueeze(0), batch["qtype"], mask, w_sph=0.75, w_rps=1.0
                )
                adv = reward - reward.mean(0, keepdim=True)
                adv = adv / (adv.std() + 1e-6)
            logp = -(((z - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma**2)
            loss_rl = -(adv * logp).mean()
            loss_ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1))
            loss = (loss_rl + loss_ce.sum(-1).mean()) / cfg.grad_accum + 0.0 * act.sum()
            scaler.scale(loss).backward()
            if (index + 1) % cfg.grad_accum == 0 or index + 1 == n_batches:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                if time_is_up():
                    save_rolling(epoch, index + 1)
                    stopped = True
                    break
            total_loss += loss.item() * cfg.grad_accum
            batches += 1
            if rank == 0 and time.time() - last_print > 60:
                last_print = time.time()
                say(
                    f"  Laya: epoch {epoch + 1}/{cfg.epochs} · batch {index + 1}/{n_batches} · "
                    f"loss {loss.item() * cfg.grad_accum:.4f} · {fmt_s(time.time() - started)}"
                )
        if stopped:
            if rank == 0:
                say("Laya: 20 minutes of the session left: checkpoint saved, stopping.")
            break
        history.append(round(total_loss / max(1, batches), 4))
        if rank == 0:
            say(
                f"  Laya: epoch {epoch + 1}/{cfg.epochs} done · mean loss {history[-1]:.4f} · "
                f"{fmt_s(time.time() - started)}"
            )
        save_rolling(epoch + 1, 0)
        if world > 1:
            dist.barrier()
    result: dict[str, Any] = {"status": "incomplete" if stopped else "done", "loss": history}
    if not stopped and rank == 0:
        model.eval()
        preds = []
        with torch.no_grad():
            for i in range(0, len(calib), 16):
                chunk = calib[i : i + 16]
                batch = {k: v.to(device) for k, v in _collate(chunk, tok.pad_token_id).items()}
                with torch.autocast("cuda", dtype=torch.float16, enabled=cuda):
                    logits, _ = model(
                        batch["input_ids"],
                        batch["attention_mask"],
                        batch["marker_pos"],
                        batch["marker_mask"],
                        batch["qtype"],
                    )
                values = logits.float().cpu().numpy()
                for r, it in enumerate(chunk):
                    preds.append((it["qtype"], values[r, : len(it["markers"])], it["target"]))
        temps = [
            _fit_temperature([(z, t) for qt, z, t in preds if qt == kind]) for kind in range(3)
        ]
        out.mkdir(parents=True, exist_ok=True)
        save_file(
            {k: v.half().contiguous().cpu() for k, v in model.state_dict().items()},
            out / "model.safetensors",
        )
        model.encoder.config.save_pretrained(out / "encoder")
        tok.save_pretrained(out / "tokenizer")
        saved_cfg = {
            **run_cfg,
            "fine_tuned": True,
            "model_name": "tempo-router-judge",
            "temperature": temps,
        }
        saved_cfg.pop("temperature_by_options", None)  # the fit above is per type
        (out / "rl_agent_config.json").write_text(json.dumps(saved_cfg, indent=2), "utf-8")
        result["temperatures"] = [round(t, 3) for t in temps]
        result["seconds"] = round(time.time() - started)
        (out / "laya-train-result.json").write_text(json.dumps(result), encoding="utf-8")
        say(f"Laya: saved {out} (calibration temperatures {result['temperatures']})")
    elif rank == 0:
        (out / "laya-train-result.json").write_text(json.dumps(result), encoding="utf-8")
    if world > 1:
        dist.destroy_process_group()
    return result


def run_laya_training(
    model_dir: Path,
    work: Path,
    rows: list[dict[str, Any]],
    cfg: LayaConfig,
    clock: Clock,
    state: State,
) -> dict[str, Any]:
    """Tokenize, then train on every GPU (torchrun when there are two), or on the CPU."""
    import torch

    if state.done("laya"):
        say("Laya: already trained (resumed run).")
        return state.get("laya")
    items_path = state.dir / "laya_items.pt"
    if not items_path.exists():
        items = laya_items(rows, model_dir)
        if not items:
            raise SystemExit("No usable Laya training items in the pack.")
        torch.save(items, items_path)
        say(f"Laya: {len(items)} training items from {len(rows)} rows.")
    cfg.budget_s, cfg.started = clock.budget_s, clock.started
    out = work / "tempo-router-judge"
    rolling_dir = state.dir / "laya"
    gpus = torch.cuda.device_count()
    started = time.time()
    if gpus >= 2:
        config = state.dir / "laya-config.json"
        config.write_text(
            json.dumps(
                {
                    "model_dir": str(model_dir),
                    "out": str(out),
                    "rolling": str(rolling_dir),
                    "items": str(items_path),
                    "cfg": asdict(cfg),
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                "torchrun",
                "--standalone",
                f"--nproc_per_node={gpus}",
                __file__,
                "laya-train",
                str(config),
            ],
            check=True,
        )
        result_path = rolling_dir / "laya-train-result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = _laya_train_to(model_dir, out, rolling_dir, items_path, cfg)
    result["seconds"] = round(time.time() - started)
    if result["status"] == "done":  # the optimizer state is large (GBs) and no longer needed
        shutil.rmtree(rolling_dir, ignore_errors=True)
        items_path.unlink(missing_ok=True)
    state.mark(
        "laya",
        result["status"],
        loss=result.get("loss"),
        temperatures=result.get("temperatures"),
        seconds=result["seconds"],
    )
    return state.get("laya")


def _laya_train_to(
    model_dir: Path, out: Path, rolling_dir: Path, items: Path, cfg: LayaConfig
) -> dict[str, Any]:
    """Train with the rolling checkpoint in ``rolling_dir`` and the final model in ``out``."""
    result = laya_train(model_dir, rolling_dir, items, cfg)
    if result["status"] == "done" and int(os.environ.get("RANK", "0")) == 0:
        out.mkdir(parents=True, exist_ok=True)
        for name in ("model.safetensors", "rl_agent_config.json"):
            shutil.copy2(rolling_dir / name, out / name)
        for sub in ("encoder", "tokenizer"):
            shutil.copytree(rolling_dir / sub, out / sub, dirs_exist_ok=True)
    return result


def laya_eval(checkpoint: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy per decision on held-out rows (the label with the highest probability against
    the outcome label), as `tempo-server models compare` scores it."""
    import laya
    import torch

    agent = laya.Agent(str(checkpoint), device="cuda" if torch.cuda.is_available() else "cpu")
    return score_laya(lambda state, qs: agent.predict(state, qs)["answers"], rows)


def predicted_label(qdef: dict[str, Any], answer: dict[str, Any]) -> str:
    if qdef["type"] == "choice":
        return str(answer["choice"])
    if qdef["type"] == "noul":
        return "true" if answer["noul"] >= 0.5 else "false"
    probs = answer.get("probabilities") or {}
    if probs:
        return max(probs, key=lambda k: probs[k])
    return str(round(answer.get("score", 0)))


def score_laya(predict: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    tallies: dict[str, list[int]] = {}
    started = time.time()
    for row in rows:
        questions = json.loads(row["questions"])
        gold = json.loads(row["gold"])
        answers = predict(json.loads(row["state"]), questions)
        for qid, qdef in questions.items():
            if qid not in gold or qid not in answers:
                continue
            tally = tallies.setdefault(qid, [0, 0])
            tally[0] += 1
            tally[1] += int(predicted_label(qdef, answers[qid]) == str(gold[qid]["label"]))
    per = {k: {"n": n, "accuracy": round(right / n, 4)} for k, (n, right) in tallies.items() if n}
    n = sum(v["n"] for v in per.values())
    right = sum(v["n"] * v["accuracy"] for v in per.values())
    return {
        "decisions": dict(sorted(per.items())),
        "n": n,
        "accuracy": round(right / n, 4) if n else None,
        "ms_per_row": round((time.time() - started) * 1000 / max(1, len(rows)), 1),
    }


# --- reports ---------------------------------------------------------------------------------


def write_report(out: Path, report: dict[str, Any]) -> Path:
    """``report.json`` and a short ``REPORT.md`` next to the model files."""
    out.mkdir(parents=True, exist_ok=True)
    report = {"kit_version": KIT_VERSION, **report}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"# {report['model']} training report", ""]
    lines.append(
        f"- Status: **{report['status']}**"
        + (
            " (Kaggle's time limit: add this version's output as input and run again to resume)"
            if report["status"] != "complete"
            else ""
        )
    )
    lines.append(f"- Run: {report['run_id']} · {report.get('hardware', '')}")
    lines.append(f"- Base: {report['base']} ({report['base_licence']}, {report['base_source']})")
    for name, info in report.get("stages", {}).items():
        shown = {k: v for k, v in info.items() if k != "files"}
        lines.append(f"- {name}: " + ", ".join(f"{k} {v}" for k, v in shown.items()))
    heldout = report.get("heldout")
    if heldout:
        lines += ["", "## Held-out scores", ""]
        lines += [f"- {k}: {json.dumps(v)}" for k, v in heldout.items()]
    manifest = report.get("data", {})
    if manifest:
        mix = manifest.get("mix", {})
        lines += ["", "## Data", ""]
        lines.append(
            "- Counts: "
            + ", ".join(
                f"{k} {v['train']} train / {v['test']} test"
                for k, v in manifest.get("counts", {}).items()
            )
        )
        lines.append(
            f"- Mix: {mix.get('public_share', 0):.0%} public or human, "
            f"{mix.get('self_share', 0):.0%} from an earlier Tempo-Core"
        )
        lines += ["", "## Licences", ""]
        lines.append(f"- Base model: {report['base_licence']}")
        for kind, info in manifest.get("licences", {}).items():
            sources = ", ".join(
                f"{n} ({s['license']}, {s['rows']} rows)" for n, s in info["sources"].items()
            )
            lines.append(f"- {kind} sources: {sources or 'none'}")
            answers = ", ".join(f"{k} {v}" for k, v in info["answer_model_licences"].items())
            lines.append(f"- {kind} answer models: {answers or 'none'}")
    if report.get("files"):
        lines += ["", "## Files", ""]
        lines += [
            f"- {k}: {v['bytes'] / 1e6:.1f} MB, sha256 {v['sha256']}"
            for k, v in report["files"].items()
        ]
    lines += [
        "",
        "Next: download this notebook's output, then `tempo-server models import "
        "<zip>`, `tempo-server models compare`, `tempo-server models promote`.",
    ]
    path = out / "REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return path


def run_id() -> str:
    return time.strftime("%Y%m%d-%H%M", time.gmtime())


def main(argv: list[str]) -> None:
    """Entry point for ``torchrun tempo_trainkit.py laya-train <config.json>``."""
    if len(argv) == 2 and argv[0] == "laya-train":
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        _laya_train_to(
            Path(spec["model_dir"]),
            Path(spec["out"]),
            Path(spec["rolling"]),
            Path(spec["items"]),
            LayaConfig(**spec["cfg"]),
        )
        return
    raise SystemExit("usage: tempo_trainkit.py laya-train <config.json>")


if __name__ == "__main__":
    main(sys.argv[1:])
