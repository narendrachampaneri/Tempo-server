"""`tempo-server train dry-run`: every step of the training loop on a CPU, with no keys and no
GPU, before real data exists.

1. demo data: built-in questions answered by the offline demo models (a private data folder);
2. ``train prepare``: exports, checks, the pack;
3. both notebooks, executed cell by cell as Kaggle would, with tiny models (100 LoRA steps on
   a 40M-parameter copy of Qwen3's architecture; a small Laya);
4. ``models import`` of both outputs, zipped like Kaggle's download;
5. ``models compare`` (old = the untuned tiny base) and ``models promote``.

It proves the plumbing, not quality: tiny random models write nonsense, so the Tempo-Core gate
is expected to keep the old version.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import shutil
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

NEEDS = ("torch", "transformers", "peft", "trl", "datasets", "laya", "sentencepiece", "gguf")


class DryRunError(Exception):
    pass


def missing_packages() -> list[str]:
    return [name for name in NEEDS if importlib.util.find_spec(name) is None]


@contextlib.contextmanager
def _environment(values: dict[str, str]) -> Iterator[None]:
    keep = dict(os.environ)
    os.environ.update(values)
    for name in ("TEMPO_LAYA_MODEL", "TEMPO_API_KEY"):
        if name not in values:
            os.environ.pop(name, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(keep)


async def _demo_data(engine: Any, count: int, say: Callable[[str], None]) -> int:
    from tempo.accounts import LOCAL_USER
    from tempo.collect import COLLECT_USER
    from tempo.datasets import DEMO_DATASET, demo_items
    from tempo.types import Access

    options = engine.options(
        mode="auto",
        use_cache=False,
        live=False,
        access=Access(user_id=COLLECT_USER, user_keys=engine.access_for(LOCAL_USER).user_keys),
    )
    gate = asyncio.Semaphore(4)
    done = 0

    async def one(item: Any) -> None:
        nonlocal done
        async with gate:
            result = await engine.complete([{"role": "user", "content": item.text}], options)
        if result.error is None:
            engine.store.collect_mark(
                DEMO_DATASET.name, item.item_id, "done", question_id=result.question_id
            )
            done += 1

    await asyncio.gather(*(one(item) for item in demo_items(count)))
    say(f"  {done} of {count} demo questions answered and checked by the demo models")
    return done


def _zip(folder: Path, parts: list[str], archive: Path) -> Path:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for part in parts:
            root = folder / part
            paths = [root] if root.is_file() else sorted(root.rglob("*"))
            for path in paths:
                if path.is_file():
                    z.write(path, path.relative_to(folder).as_posix())
    return archive


def run(work: Path, questions: int = 40, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Run the whole loop in ``work``. Returns the outcome of every step."""
    missing = missing_packages()
    if missing:
        raise DryRunError(
            f"The dry run trains tiny models, so it needs the training libraries (missing: "
            f"{', '.join(missing)}): pip install 'tempo-server[train]' (docs/TRAINING.md)."
        )
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    kaggle = work / "kaggle"
    llama_cache = Path(os.environ.get("TEMPO_LLAMA_CPP") or work / "llama.cpp")
    env = {
        "TEMPO_ENABLE_MOCK": "1",
        "TEMPO_DATA_DIR": str(work / "data"),
        "TEMPO_LAYA": "off",
        "TEMPO_EMBEDDINGS": "off",
        "TEMPO_SYNC_INTERVAL": "0",
        "TEMPO_LLAMA_CPP": str(llama_cache),
        "TEMPO_LLAMA_SERVER": str(llama_cache / "llama-b11249" / "llama-server"),
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "TRANSFORMERS_VERBOSITY": "error",
        "TOKENIZERS_PARALLELISM": "false",
    }
    outcome: dict[str, Any] = {"work": str(work), "steps": {}}
    started = time.time()

    def step(name: str) -> Any:
        say(f"\n== {name}")
        return time.time()

    def finished(name: str, since: float, **info: Any) -> None:
        outcome["steps"][name] = {"seconds": round(time.time() - since, 1), **info}
        say(f"   ({time.time() - since:.0f} s)")

    with _environment(env):
        from tempo import model_loop, notebooks, training
        from tempo.config import Settings
        from tempo.engine import Engine
        from tempo.sft import DEFAULT_USERS

        engine = Engine.from_settings(Settings.from_env())
        asyncio.run(engine.startup(oneshot=True))

        t = step("1. Demo data (offline demo models, no keys)")
        answered = asyncio.run(_demo_data(engine, questions, say))
        finished("demo_data", t, questions=answered)

        t = step("2. tempo-server train prepare")
        report = training.prepare(
            engine, work / "pack" / "tempo-training", test_percent=25, users=set(DEFAULT_USERS)
        )
        for finding in report.findings:
            say(f"   {finding.level}: {finding.text}")
        if report.zip_path is None:
            raise DryRunError("prepare did not pack the demo data")
        say(f"   packed {report.zip_path.name}: {report.manifest['counts']}")
        finished("prepare", t, counts=report.manifest["counts"])

        inputs = kaggle / "input" / "tempo-training"
        shutil.rmtree(kaggle, ignore_errors=True)
        inputs.mkdir(parents=True)
        with zipfile.ZipFile(report.zip_path) as z:
            z.extractall(inputs)  # as Kaggle unpacks an uploaded zip

        downloads = {}
        for name, folder, parts in (
            ("tempo_core.ipynb", "core", ["tempo-core", "checkpoints/state.json"]),
            ("tempo_router_judge.ipynb", "laya", ["tempo-router-judge", "checkpoints/state.json"]),
        ):
            t = step(f"3. Kaggle notebook {name} (CPU, tiny models)")
            notebooks.run(name, kaggle / "input", kaggle / folder, say=say)
            downloads[folder] = _zip(kaggle / folder, parts, kaggle / f"{folder}-output.zip")
            finished(f"notebook_{folder}", t)

        t = step("4. tempo-server models import (both downloads)")
        imported = []
        for archive in downloads.values():
            for item in model_loop.import_model(engine, archive):
                say(f"   {item.kind} {item.version} -> {item.path}")
                for note in item.notes:
                    say(f"     {note}")
                imported.append({"kind": item.kind, "version": item.version})
        finished("import", t, imported=imported)

        t = step("5. tempo-server models compare")
        core = model_loop.compare_core(
            engine, report.folder, old="base", min_questions=3, max_tokens=48, say=say
        )
        for row in core["types"]:
            say(
                f"   tempo-core {row['task']}: old {row['old']:.3f} new {row['new']:.3f} "
                f"({row['reason']})"
            )
        say(
            "   tempo-core: "
            + ("passed" if core["release"] else "old version stays")
            + ": "
            + "; ".join(core["reasons"])
        )
        laya = model_loop.compare_laya(
            engine, report.folder, old=str(kaggle / "laya" / "tiny-laya"), min_rows=3, say=say
        )
        say(
            f"   laya accuracy: old {laya['accuracy']['old']} new {laya['accuracy']['new']}; "
            + "; ".join(laya["reasons"])
        )
        finished("compare", t, core_release=core["release"], laya_release=laya["release"])

        t = step("6. tempo-server models promote")
        promoted = {}
        for kind in (model_loop.CORE, model_loop.LAYA):
            try:
                change = model_loop.promote(engine, kind)
            except model_loop.LoopError as exc:  # passed, but Ollama is not running here
                promoted[kind] = False
                say(f"   {kind}: {exc}")
                continue
            promoted[kind] = change["changed"]
            say(f"   {kind}: " + ("promoted" if change["changed"] else "not promoted (gate)"))
        finished("promote", t, promoted=promoted)
    outcome["seconds"] = round(time.time() - started)
    say(f"\nDry run finished in {outcome['seconds']} s: every step of the loop ran.")
    return outcome
