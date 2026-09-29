"""Bring trained models back into Tempo-server: import, compare old and new, promote.

- ``import_model``: a notebook's output (the downloaded zip, its folder, or a ``.gguf`` file).
  A Tempo-Core GGUF is copied to ``<data>/models/tempo-core/<version>/`` and registered with
  Ollama as ``tempo-core:<version>``; a Laya checkpoint is copied to
  ``<data>/models/laya/<version>/`` and test-loaded. Checksums from the notebook's report are
  verified. Nothing is used for answers until it is promoted.
- ``compare``: runs the pack's held-out questions on the old and the new version and applies
  the promotion gate (docs/TEMPO_MODELS.md §3): per task type (Tempo-Core) or per decision
  (Laya), the new version takes over only with enough held-out questions and no drop; a
  Tempo-Core version is released only if it wins overall (paired wins and a 95% bootstrap
  interval above zero), is not more repetitive, and is fast enough on this CPU.
- ``promote``: applies the last comparison. Tempo-Core versions are routed per task type
  (``<data>/models/tempo-core-routes.json``, read by the registry when it discovers Ollama
  models); a Laya checkpoint becomes ``TEMPO_LAYA_MODEL`` in ``settings.env``.

No GPU and no training libraries are needed (Laya comparisons need ``tempo-server[laya]``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tempo import trainkit
from tempo.registry import OLLAMA_DEFAULT_BASE

if TYPE_CHECKING:
    from tempo.engine import Engine

CORE = "tempo-core"
LAYA = "laya"
ROUTES_FILE = "tempo-core-routes.json"
MIN_CORE_QUESTIONS = 30  # per task type (owner's decision, step 4)
MIN_LAYA_ROWS = 50  # per decision, as `tempo-server laya compare`
REPETITION_TOLERANCE = 0.05  # a new version may not be 5% more repetitive (§4)
MIN_TOKENS_PER_SECOND = 8.0  # on this computer's CPU (TEMPO_MODELS.md: 4B needs 8 on 4 threads)
BASE_LICENCE_TEXT = (
    "Apache License, Version 2.0. Tempo-Core is a fine-tune of {base}, which is licensed under "
    "the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0)."
)


class LoopError(Exception):
    """A problem the user can fix; the message says how."""


# --- where things live ----------------------------------------------------------------------


def models_dir(engine: Engine) -> Path:
    if engine.settings.data_dir is None:
        raise LoopError("Importing models needs a data directory (TEMPO_DATA_DIR).")
    return Path(engine.settings.data_dir) / "models"


def _book_path(engine: Engine) -> Path:
    return models_dir(engine) / "models.json"


def book(engine: Engine) -> dict[str, Any]:
    """Imported versions, the last comparison per kind, and promotions."""
    path = _book_path(engine)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"imports": [], "compare": {}, "promotions": []}


def _save_book(engine: Engine, data: dict[str, Any]) -> None:
    path = _book_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def imported(engine: Engine, kind: str) -> list[dict[str, Any]]:
    return [i for i in book(engine)["imports"] if i["kind"] == kind]


def load_routes(data_dir: Path | None) -> dict[str, str]:
    """task type -> Tempo-Core version (``tempo-core:<version>``) that answers it."""
    if data_dir is None:
        return {}
    path = Path(data_dir) / "models" / ROUTES_FILE
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")).get("routes", {}))
    except (OSError, ValueError):
        return {}


# --- import ---------------------------------------------------------------------------------


@dataclass
class Imported:
    kind: str
    version: str
    path: Path
    ollama: str | None = None  # the Ollama model name, when registered
    notes: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _unpack(path: Path, scratch: Path) -> Path:
    if path.is_file() and path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    raise LoopError(f"unsafe path in {path.name}: {name}")
            archive.extractall(scratch)
        return scratch
    return path


def _find(root: Path) -> list[tuple[str, Path]]:
    """(kind, folder or file) for every trained model under ``root``."""
    if root.is_file():
        if root.suffix == ".gguf":
            return [(CORE, root)]
        raise LoopError(f"{root} is not a .gguf file, a folder or a zip")
    found = []
    for config in sorted(root.rglob("rl_agent_config.json")):
        folder = config.parent
        if (folder / "model.safetensors").exists() and "checkpoint_latest" not in folder.parts:
            found.append((LAYA, folder))
    for gguf in sorted(root.rglob("tempo-core-*.gguf")):
        found.append((CORE, gguf))
    if not found:
        raise LoopError(
            f"No trained model in {root}: expected a Tempo-Core GGUF (tempo-core-*.gguf) or a "
            "Laya checkpoint (rl_agent_config.json + model.safetensors), as the notebooks write."
        )
    return found


def _report(folder: Path) -> dict[str, Any]:
    path = folder / "report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _verify(folder: Path, report: dict[str, Any], names: list[str]) -> None:
    files = report.get("files") or {}
    for name in names:
        expected = (files.get(name) or {}).get("sha256")
        if expected and _sha256(folder / name) != expected:
            raise LoopError(f"{name}: checksum differs from the notebook's report; download again")


def _version(report: dict[str, Any], path: Path) -> str:
    if report.get("run_id"):
        return str(report["run_id"])
    return time.strftime("%Y%m%d-%H%M", time.gmtime(path.stat().st_mtime))


def ollama_base(engine: Engine) -> str:
    try:
        base = engine.registry.credentials("ollama").get("api_base")
    except Exception:  # noqa: BLE001 - no Ollama provider configured
        base = None
    return (base or os.environ.get("OLLAMA_API_BASE") or OLLAMA_DEFAULT_BASE).rstrip("/")


def register_ollama(
    base: str,
    name: str,
    gguf: Path,
    base_model: str,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """``ollama create`` over Ollama's HTTP API: upload the GGUF as a blob, then create the
    model with Tempo-Core's chat template, stop word, sampling and licence."""
    digest = "sha256:" + _sha256(gguf)
    with httpx.Client(transport=transport, timeout=httpx.Timeout(600, connect=5)) as client:
        head = client.head(f"{base}/api/blobs/{digest}")
        if head.status_code != 200:
            with gguf.open("rb") as handle:
                client.post(f"{base}/api/blobs/{digest}", content=handle).raise_for_status()
        response = client.post(
            f"{base}/api/create",
            json={
                "model": name,
                "files": {gguf.name: digest},
                "template": trainkit.MODELFILE_TEMPLATE,
                "parameters": {
                    "stop": ["<|im_end|>"],
                    "temperature": 0.7,
                    "top_p": 0.8,
                    "top_k": 20,
                },
                "license": BASE_LICENCE_TEXT.format(base=base_model),
                "stream": False,
            },
        )
        response.raise_for_status()


def import_model(
    engine: Engine,
    path: Path,
    *,
    ollama: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> list[Imported]:
    """Import every trained model found at ``path`` (zip, folder or ``.gguf``)."""
    if not path.exists():
        raise LoopError(f"{path} does not exist")
    results = []
    with tempfile.TemporaryDirectory() as scratch:
        for kind, found in _find(_unpack(path, Path(scratch))):
            if kind == CORE:
                results.append(_import_core(engine, found, ollama, transport))
            else:
                results.append(_import_laya(engine, found))
    data = book(engine)
    for item in results:
        data["imports"] = [
            i for i in data["imports"] if (i["kind"], i["version"]) != (item.kind, item.version)
        ]
        data["imports"].append(
            {
                "kind": item.kind,
                "version": item.version,
                "path": str(item.path),
                "ollama": item.ollama,
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": item.report.get("status"),
                "dry_run": bool(item.report.get("dry_run")),
                "ollama_base": item.report.get("ollama_base"),
                "heldout": item.report.get("heldout"),
            }
        )
    _save_book(engine, data)
    return results


def _import_core(
    engine: Engine, gguf: Path, ollama: bool, transport: httpx.BaseTransport | None
) -> Imported:
    folder = gguf.parent
    report = _report(folder)
    _verify(folder, report, [gguf.name])
    if report and report.get("status") != "complete":
        raise LoopError("This Tempo-Core run is incomplete (see its REPORT.md); resume it first.")
    version = _version(report, gguf)
    target = models_dir(engine) / CORE / version
    target.mkdir(parents=True, exist_ok=True)
    for item in [gguf, *(folder / n for n in ("Modelfile", "report.json", "REPORT.md"))]:
        if item.exists():
            shutil.copy2(item, target / item.name)
    if (folder / "base.gguf").exists():  # dry runs compare against the untuned tiny base
        shutil.copy2(folder / "base.gguf", target / "base.gguf")
    if not (target / "Modelfile").exists():
        (target / "Modelfile").write_text(trainkit.modelfile(gguf.name), encoding="utf-8")
    result = Imported(CORE, version, target, report=report)
    name = f"{CORE}:{version}"
    if not ollama:
        result.notes.append(
            f"Not registered with Ollama (--no-ollama). Later: ollama create "
            f"{name} -f {target / 'Modelfile'}"
        )
        return result
    base = ollama_base(engine)
    try:
        register_ollama(
            base, name, target / gguf.name, report.get("base", "Qwen/Qwen3-1.7B"), transport
        )
        result.ollama = name
        result.notes.append(f"Registered with Ollama at {base} as {name}.")
    except httpx.HTTPError as exc:
        result.notes.append(
            f"Ollama at {base} is not reachable or refused the model ({exc}). Start Ollama and "
            f"run this again, or: ollama create {name} -f {target / 'Modelfile'}"
        )
    return result


def _import_laya(engine: Engine, folder: Path) -> Imported:
    report = _report(folder)
    _verify(folder, report, ["model.safetensors", "rl_agent_config.json"])
    if report and report.get("status") != "complete":
        raise LoopError("This Laya run is incomplete (see its REPORT.md); resume it first.")
    for part in ("encoder/config.json", "tokenizer"):
        if not (folder / part).exists():
            raise LoopError(f"{folder} is not a whole Laya checkpoint: {part} is missing")
    version = _version(report, folder / "model.safetensors")
    target = models_dir(engine) / LAYA / version
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(folder, target)
    result = Imported(LAYA, version, target, report=report)
    try:
        import laya  # noqa: F401
    except ImportError:
        result.notes.append(
            "Laya is not installed here (pip install 'tempo-server[laya]'), "
            "so the checkpoint was not test-loaded."
        )
        return result
    agent = laya.Agent(str(target), device="cpu")
    agent.predict({"question": "hi"}, {"ok": {"type": "noul", "instructions": "Is this a test?"}})
    result.notes.append("Test-loaded with Laya on the CPU.")
    return result


# --- runners: how the old and new versions answer -------------------------------------------


@dataclass
class Reply:
    text: str
    tokens_per_second: float | None = None
    error: str | None = None


class OllamaRunner:
    def __init__(
        self, base: str, model: str, max_tokens: int, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base, self.model, self.max_tokens = base, model, max_tokens
        self.label = f"ollama:{model}"
        self.client = httpx.Client(transport=transport, timeout=httpx.Timeout(300, connect=5))

    def __enter__(self) -> OllamaRunner:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.close()

    def answer(self, messages: list[dict[str, Any]]) -> Reply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_predict": self.max_tokens, "seed": 17},
        }
        response = self.client.post(f"{self.base}/api/chat", json=body)
        if response.status_code == 400 and "think" in response.text:
            body.pop("think")  # a model without a thinking mode (Tempo-Core's own template)
            response = self.client.post(f"{self.base}/api/chat", json=body)
        if response.status_code != 200:
            return Reply("", error=f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        tps = None
        if data.get("eval_count") and data.get("eval_duration"):
            tps = data["eval_count"] / (data["eval_duration"] / 1e9)
        return Reply((data.get("message") or {}).get("content") or "", tps)


class LlamaServerRunner:
    """A GGUF file on llama.cpp's own server (CPU), for machines without Ollama and for the
    dry run."""

    def __init__(self, server: Path, gguf: Path, max_tokens: int, threads: int | None = None):
        self.server, self.gguf, self.max_tokens = server, gguf, max_tokens
        self.threads = threads or min(4, os.cpu_count() or 4)
        self.label = f"gguf:{gguf.name}"
        self.process: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> LlamaServerRunner:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.process = subprocess.Popen(
            [
                str(self.server),
                "-m",
                str(self.gguf),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "-c",
                "4096",
                "-t",
                str(self.threads),
                "--jinja",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.client = httpx.Client(timeout=httpx.Timeout(300, connect=5), trust_env=False)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise LoopError(f"llama-server stopped while loading {self.gguf.name}")
            try:
                if self.client.get(f"http://127.0.0.1:{self.port}/health").status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        self.__exit__()
        raise LoopError(f"llama-server did not start for {self.gguf.name}")

    def __exit__(self, *exc: Any) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.client.close()

    def answer(self, messages: list[dict[str, Any]]) -> Reply:
        response = self.client.post(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            json={
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "seed": 17,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        if response.status_code != 200:
            return Reply("", error=f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        tps = (data.get("timings") or {}).get("predicted_per_second")
        return Reply(data["choices"][0]["message"].get("content") or "", tps)


def llama_server_path(explicit: Path | None = None) -> Path | None:
    for candidate in (
        explicit,
        os.environ.get("TEMPO_LLAMA_SERVER"),
        shutil.which("llama-server"),
    ):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def core_runner(
    engine: Engine,
    spec: str,
    max_tokens: int,
    llama_server: Path | None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    """``ollama:NAME``, ``gguf:PATH``, ``base`` (the untuned base), or an imported version
    (``20260929-1007``, ``tempo-core:<version>``, ``latest``)."""
    if spec.startswith("ollama:"):
        return OllamaRunner(ollama_base(engine), spec[7:], max_tokens, transport)
    if spec.startswith("gguf:"):
        server = llama_server_path(llama_server)
        if server is None:
            raise LoopError(
                "A .gguf needs llama.cpp's llama-server (--llama-server PATH or "
                "TEMPO_LLAMA_SERVER), or import it into Ollama and use ollama:NAME."
            )
        return LlamaServerRunner(server, Path(spec[5:]), max_tokens)
    versions = imported(engine, CORE)
    if spec == "base":
        latest = versions[-1] if versions else {}
        local_base = Path(latest.get("path", "")) / "base.gguf"
        if latest and local_base.exists():
            return core_runner(engine, f"gguf:{local_base}", max_tokens, llama_server, transport)
        name = latest.get("ollama_base") or trainkit.CORE_BASES["Qwen/Qwen3-1.7B"]["ollama_base"]
        return OllamaRunner(ollama_base(engine), name, max_tokens, transport)
    wanted = spec.removeprefix(f"{CORE}:")
    match = (
        versions[-1]
        if wanted == "latest" and versions
        else next((v for v in versions if v["version"] == wanted), None)
    )
    if match is None:
        raise LoopError(f"No imported Tempo-Core version {spec!r} (tempo-server models import)")
    if match.get("ollama"):
        return OllamaRunner(ollama_base(engine), match["ollama"], max_tokens, transport)
    gguf = next(Path(match["path"]).glob("tempo-core-*.gguf"))
    return core_runner(engine, f"gguf:{gguf}", max_tokens, llama_server, transport)


# --- grading ---------------------------------------------------------------------------------


async def grade(engine: Engine, question: str, answer: str, answer_family: str) -> float | None:
    """0-1: Tempo's quick checks and a judge from another family (assist.verify), and for code
    and arithmetic, a run in the sandbox (a failed run scores 0). None: could not grade."""
    from tempo import assist

    if not answer.strip():
        return 0.0
    verdict = await assist.verify(engine, question, answer, answer_model=answer_family)
    if verdict.get("quick_checks", {}).get("hard_fail"):
        return 0.0
    if verdict.get("score") is None:
        return None
    score = float(verdict["score"]) / 10
    if engine.sandbox is not None and await asyncio.to_thread(
        _sandbox_failed, engine, question, answer
    ):
        return 0.0
    return score


def _sandbox_failed(engine: Engine, question: str, answer: str) -> bool:
    from tempo import execute

    sandbox = engine.sandbox
    assert sandbox is not None
    program = execute.build_program(question, answer)
    if program is not None and sandbox.available(program.language):
        return execute.run_code(sandbox, program).status == "failed"
    expression = execute.math_expression(question)
    if expression and sandbox.available("python"):
        result = execute.check_math(sandbox, answer, execute.math_program(expression), "rules")
        return result.status == "failed"
    return False


# --- the gate --------------------------------------------------------------------------------


@dataclass
class TypeResult:
    task: str
    n: int
    old: float
    new: float
    wins: int
    losses: int
    takes_over: bool
    reason: str


def bootstrap_low(diffs: list[float], rounds: int = 2000, seed: int = 17) -> float:
    """Lower end of the 95% bootstrap interval of the mean difference (new - old)."""
    if not diffs:
        return 0.0
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(rounds))
    return means[int(0.025 * rounds)]


def per_type(pairs: list[dict[str, Any]], min_n: int) -> list[TypeResult]:
    """The rule per task type: at least ``min_n`` held-out questions and no drop."""
    by: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        by.setdefault(pair["task_type"] or "?", []).append(pair)
    results = []
    for task, items in sorted(by.items()):
        old = sum(p["old_score"] for p in items) / len(items)
        new = sum(p["new_score"] for p in items) / len(items)
        wins = sum(p["new_score"] > p["old_score"] for p in items)
        losses = sum(p["new_score"] < p["old_score"] for p in items)
        if len(items) < min_n:
            takes, reason = False, f"only {len(items)} held-out questions (needs {min_n})"
        elif new < old:
            takes, reason = False, f"score dropped by {old - new:.3f}"
        else:
            takes, reason = True, f"no drop ({new - old:+.3f})"
        results.append(
            TypeResult(task, len(items), round(old, 4), round(new, 4), wins, losses, takes, reason)
        )
    return results


def release_check(
    pairs: list[dict[str, Any]],
    types: list[TypeResult],
    repetition: dict[str, dict[str, float]],
    speed: float | None,
    min_speed: float,
) -> tuple[bool, list[str]]:
    """Whether the new version may be released at all, with the reasons."""
    reasons = []
    taken = {t.task for t in types if t.takes_over}
    mine = [p for p in pairs if (p["task_type"] or "?") in taken]
    wins = sum(p["new_score"] > p["old_score"] for p in mine)
    losses = sum(p["new_score"] < p["old_score"] for p in mine)
    low = bootstrap_low([p["new_score"] - p["old_score"] for p in mine])
    ok = True
    if not taken:
        ok = False
        reasons.append("no task type passed the gate")
    else:
        need = "" if wins > losses else " (needs more wins than losses)"
        ok = ok and not need
        reasons.append(f"paired results on the types it takes: {wins} wins, {losses} losses{need}")
        need = "" if low > 0 else " (must be above zero)"
        ok = ok and not need
        reasons.append(f"95% bootstrap interval of the score gain starts at {low:+.3f}{need}")
    old, new = repetition["old"], repetition["new"]
    if (
        new["distinct_2"] < old["distinct_2"] * (1 - REPETITION_TOLERANCE)
        or new["repeated_4"] > old["repeated_4"] * (1 + REPETITION_TOLERANCE) + 0.01
    ):
        ok = False
        reasons.append(f"more repetitive than the old version ({new} vs {old})")
    else:
        reasons.append("repetition check passed")
    if speed is None:
        reasons.append("speed not measured")
    elif speed < min_speed:
        ok = False
        reasons.append(f"{speed:.1f} tokens/s on this CPU, below {min_speed:g}")
    else:
        reasons.append(f"{speed:.1f} tokens/s on this CPU")
    return ok, reasons


# --- compare ---------------------------------------------------------------------------------


def _pack_rows(pack: Path | None, kind: str) -> list[dict[str, Any]]:
    if pack is None:
        raise LoopError("No held-out set: run `tempo-server train prepare` first, or pass --data.")
    rows = trainkit.read_jsonl(pack / kind / "test.jsonl")
    if not rows:
        raise LoopError(f"{pack / kind / 'test.jsonl'} has no held-out rows")
    return rows


def compare_core(
    engine: Engine,
    pack: Path | None,
    *,
    old: str = "",
    new: str = "latest",
    min_questions: int = MIN_CORE_QUESTIONS,
    min_speed: float = MIN_TOKENS_PER_SECOND,
    max_tokens: int = 512,
    limit: int | None = None,
    llama_server: Path | None = None,
    say: Callable[[str], None] = print,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    from tempo.sft import repetition

    rows = _pack_rows(pack, "sft")[:limit]
    routes = load_routes(engine.settings.data_dir)
    old = old or (sorted(set(routes.values()))[-1] if routes else "base")
    answers: dict[str, list[Reply]] = {}
    labels = {}
    for side, spec in (("old", old), ("new", new)):
        with core_runner(engine, spec, max_tokens, llama_server, transport) as runner:
            labels[side] = runner.label
            say(f"{side}: {runner.label} answering {len(rows)} held-out questions")
            answers[side] = [runner.answer(r["messages"][:-1]) for r in rows]
    speeds = [r.tokens_per_second for r in answers["new"] if r.tokens_per_second]
    speed = sum(speeds) / len(speeds) if speeds else None

    async def grade_all() -> list[dict[str, Any]]:
        out = []
        for index, row in enumerate(rows):
            question = row["messages"][-2]["content"]
            scores = []
            for side in ("old", "new"):
                reply = answers[side][index]
                scores.append(
                    None if reply.error else await grade(engine, question, reply.text, "qwen3")
                )
            if None in scores:
                continue
            out.append(
                {
                    "question_id": row["question_id"],
                    "task_type": row.get("task_type"),
                    "old_score": scores[0],
                    "new_score": scores[1],
                    "old_answer": answers["old"][index].text[:2000],
                    "new_answer": answers["new"][index].text[:2000],
                }
            )
        return out

    pairs = asyncio.run(grade_all())
    errors = sum(1 for side in answers.values() for r in side if r.error)
    types = per_type(pairs, min_questions)
    rep = {
        "old": repetition([p["old_answer"] for p in pairs]),
        "new": repetition([p["new_answer"] for p in pairs]),
    }
    release, reasons = release_check(pairs, types, rep, speed, min_speed)
    result = {
        "kind": CORE,
        "old": old,
        "new": new,
        "new_version": _resolve_version(engine, new),
        "labels": labels,
        "graded": len(pairs),
        "not_graded": len(rows) - len(pairs),
        "answer_errors": errors,
        "min_questions": min_questions,
        "types": [asdict(t) for t in types],
        "release": release,
        "reasons": reasons,
        "repetition": rep,
        "tokens_per_second": round(speed, 1) if speed else None,
        "promote": [t.task for t in types if t.takes_over] if release else [],
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pairs": pairs,
    }
    _save_compare(engine, result)
    return result


def _resolve_version(engine: Engine, spec: str) -> str:
    versions = imported(engine, CORE)
    if spec == "latest" and versions:
        return versions[-1]["version"]
    return spec.removeprefix(f"{CORE}:")


def _laya_version(engine: Engine, spec: str) -> str:
    versions = imported(engine, LAYA)
    if spec == "latest" and versions:
        return versions[-1]["version"]
    return Path(spec).name


def _save_compare(engine: Engine, result: dict[str, Any]) -> None:
    data = book(engine)
    data["compare"][result["kind"]] = result
    _save_book(engine, data)


def _laya_checkpoint(engine: Engine, spec: str) -> Path | str:
    """A folder for ``spec``: an imported version, ``latest``, ``current`` (TEMPO_LAYA_MODEL or
    the stock checkpoint), ``stock``, or a path."""
    from tempo.laya_runtime import checkpoint_dir

    versions = imported(engine, LAYA)
    if spec == "latest":
        if not versions:
            raise LoopError("No imported Laya checkpoint (tempo-server models import)")
        return Path(versions[-1]["path"])
    match = next((v for v in versions if v["version"] == spec), None)
    if match:
        return Path(match["path"])
    if spec == "current":
        return checkpoint_dir(engine.settings.laya_checkpoint, engine.settings.laya_model)
    if spec == "stock":
        return checkpoint_dir(engine.settings.laya_checkpoint, None)
    if Path(spec).is_dir():
        return Path(spec)
    raise LoopError(f"No Laya checkpoint {spec!r}")


def compare_laya(
    engine: Engine,
    pack: Path | None,
    *,
    old: str = "current",
    new: str = "latest",
    min_rows: int = MIN_LAYA_ROWS,
    say: Callable[[str], None] = print,
) -> dict[str, Any]:
    try:
        import laya
    except ImportError as exc:
        raise LoopError(
            "Comparing Laya checkpoints needs Laya: pip install 'tempo-server[laya]'"
        ) from exc
    rows = _pack_rows(pack, "laya")
    scores = {}
    for side, spec in (("old", old), ("new", new)):
        folder = _laya_checkpoint(engine, spec)
        say(f"{side}: Laya {folder} on {len(rows)} held-out rows (CPU)")
        agent = laya.Agent(str(folder), device="cpu")
        scores[side] = trainkit.score_laya(
            lambda state, qs, a=agent: a.predict(state, qs)["answers"], rows
        )
    decisions = []
    for name in sorted(set(scores["old"]["decisions"]) | set(scores["new"]["decisions"])):
        o = scores["old"]["decisions"].get(name, {"n": 0, "accuracy": 0.0})
        n = scores["new"]["decisions"].get(name, {"n": 0, "accuracy": 0.0})
        if n["n"] < min_rows:
            verdict, ok = f"only {n['n']} held-out rows (needs {min_rows})", None
        elif n["accuracy"] < o["accuracy"]:
            verdict, ok = f"accuracy dropped by {o['accuracy'] - n['accuracy']:.3f}", False
        else:
            verdict, ok = f"no drop ({n['accuracy'] - o['accuracy']:+.3f})", True
        decisions.append(
            {
                "decision": name,
                "n": n["n"],
                "old": o["accuracy"],
                "new": n["accuracy"],
                "passes": ok,
                "reason": verdict,
            }
        )
    judged = [d for d in decisions if d["passes"] is not None]
    release = (
        bool(judged)
        and all(d["passes"] for d in judged)
        and any(d["new"] > d["old"] for d in judged)
    )
    reasons = []
    if not judged:
        reasons.append(f"no decision has {min_rows}+ held-out rows")
    elif not all(d["passes"] for d in judged):
        reasons.append("a decision dropped: one checkpoint serves every decision, so it stays")
    elif not release:
        reasons.append("no decision improved")
    else:
        reasons.append("no decision dropped and at least one improved")
    result = {
        "kind": LAYA,
        "old": str(_laya_checkpoint(engine, old)),
        "new": new,
        "new_version": _laya_version(engine, new),
        "new_path": str(_laya_checkpoint(engine, new)),
        "min_rows": min_rows,
        "decisions": decisions,
        "accuracy": {"old": scores["old"]["accuracy"], "new": scores["new"]["accuracy"]},
        "release": release,
        "reasons": reasons,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_compare(engine, result)
    return result


# --- promote ---------------------------------------------------------------------------------


def promote(engine: Engine, kind: str) -> dict[str, Any]:
    """Apply the last comparison of ``kind``. Returns what changed."""
    from tempo.setup import settings_path, write_settings

    data = book(engine)
    result = data["compare"].get(kind)
    if result is None:
        raise LoopError(f"Run `tempo-server models compare --kind {kind}` first.")
    change: dict[str, Any] = {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if not result["release"]:
        change["changed"] = False
        change["reasons"] = result["reasons"]
    elif kind == CORE:
        routes = load_routes(engine.settings.data_dir)
        name = f"{CORE}:{result['new_version']}"
        entry = next(
            (i for i in imported(engine, CORE) if i["version"] == result["new_version"]), None
        )
        if entry is not None and not entry.get("ollama"):
            raise LoopError(
                f"{name} is not registered with Ollama yet: start Ollama and run "
                f"`tempo-server models import` again ({entry['path']})."
            )
        for task in result["promote"]:
            routes[task] = name
        path = models_dir(engine) / ROUTES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"routes": routes, "updated": change["at"]}, indent=2), encoding="utf-8"
        )
        engine.registry.core_routes = routes
        change.update(changed=True, routes=routes, tasks=result["promote"])
    else:
        path = result["new_path"]
        write_settings(settings_path(engine), {"TEMPO_LAYA_MODEL": path})
        change.update(changed=True, laya_model=path)
    data["promotions"].append(change)
    _save_book(engine, data)
    return change
