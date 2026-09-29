"""Laya as the fast decision-maker at every stage.

Laya (convaiinnovations/laya, Apache-2.0, ``pip install laya``) answers typed questions
(choice / score / yes-no as a two-option choice) about a text with calibrated probabilities in
one forward pass. It does not generate text. Tempo asks it three groups of questions:

    plan    before stage 1: task type, difficulty, strategy, stage budget
    assess  after each check: answer quality, stop or continue
    pick    before a stage: which model from a shortlist of at most 10

Every decision starts in shadow mode: Laya predicts, the rules decide, and both are logged so
Laya can be fine-tuned on Tempo's own outcomes. TEMPO_LAYA_TAKEOVER hands a decision to Laya
("laya"), or to Laya only once `tempo-server laya compare` shows it beats the rules on held-out data
("auto"). If Laya is missing, errors, is busy, or takes longer than the timeout (200 ms by
default), the rules decide and the reason is logged.

Laya runs in-process on one worker thread (``laya.Router(preload=True)``), never through
laya-serve, whose default port 8000 is Tempo's own.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import queue
import string
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from tempo.config import Settings
from tempo.store import Store
from tempo.types import TASKS

if TYPE_CHECKING:
    from tempo.pipeline import Pipeline
    from tempo.router import Candidate

log = logging.getLogger(__name__)

# Laya's English checkpoint reads 512 tokens (about 320 for the state), so states are trimmed.
REQUEST_CHARS = 700
ANSWER_HEAD_CHARS = 500
ANSWER_TAIL_CHARS = 150
MAX_SHORTLIST = 10  # Laya gets weak with many options; keep a choice short
# Predictions allowed in flight. One runs at a time; a timed-out one keeps running and is
# logged as "late" when it finishes, so shadow logs stay complete on slow (CPU) hardware.
MAX_PENDING = 4
# Shadow predictions nobody waits for; beyond this many queued, new ones are dropped ("busy").
MAX_BACKGROUND = 16
HOLD_POLL_S = 0.05  # how often held shadow predictions check whether they may run
MIN_COMPARE_ROWS = 50  # held-out rows needed before "auto" trusts a comparison

STAGE_BUDGET_LEVELS = [2, 3, 5, 8]
DIFFICULTY_COMPLEXITY = [0.15, 0.45, 0.7, 0.9]

PLAN_QUESTIONS: dict[str, dict[str, Any]] = {
    "task_type": {
        "type": "choice",
        "instructions": "What kind of request is this?",
        "criteria": {
            "chat": "greeting or casual conversation",
            "code": "write, fix or explain program code",
            "math": "a calculation, equation or proof",
            "reasoning": "explain, compare, analyse or plan something",
            "writing": "write or rewrite prose such as an email, story or post",
            "summarize": "shorten or summarise text that is given",
            "translate": "translate text between languages",
            "extract": "pull structured data out of text",
        },
    },
    "difficulty": {
        "type": "score",
        "instructions": "How hard is this request to answer well?",
        "criteria": [
            "trivial: a short direct answer",
            "moderate: needs some care",
            "hard: multi-step reasoning or careful code",
            "very hard: expert level or many constraints",
        ],
    },
    "strategy": {
        "type": "choice",
        "instructions": "How should the assistant answer it?",
        "criteria": {
            "single": "one model's answer with a quick check is enough",
            "cascade": "draft, check the draft, and fix it if needed",
            "mixture": "several models answer, then the best parts are merged",
            "decompose": "split into separate parts, answer each part, then combine",
        },
    },
    "stage_budget": {
        "type": "score",
        "instructions": "How many stages will a good answer need?",
        "criteria": [
            "two stages: draft and check",
            "three stages",
            "five stages",
            "eight or more stages: a long multi-part job",
        ],
    },
}

ASSESS_QUESTIONS: dict[str, dict[str, Any]] = {
    "quality": {
        "type": "score",
        "instructions": "How good is the answer for the request?",
        "criteria": [
            "wrong or useless",
            "has real errors or gaps",
            "acceptable",
            "good",
            "excellent",
        ],
    },
    # A two-option choice with neutral keys instead of a noul: the English checkpoint's
    # true/false labels can dominate a noul answer (Laya README, "Honest limits").
    "should_stop": {
        "type": "choice",
        "instructions": "Should the assistant send this answer now?",
        "criteria": {
            "A": "yes: the answer is good enough to send",
            "B": "no: improve it with another stage first",
        },
    },
}

GROUPS = {
    "task_type": "plan",
    "difficulty": "plan",
    "strategy": "plan",
    "stage_budget": "plan",
    "quality": "assess",
    "should_stop": "assess",
    "next_model": "pick",
}


def complexity_level(complexity: float) -> int:
    return 0 if complexity < 0.3 else 1 if complexity < 0.55 else 2 if complexity < 0.8 else 3


def budget_level(stages: int) -> int:
    return min(range(len(STAGE_BUDGET_LEVELS)), key=lambda i: abs(STAGE_BUDGET_LEVELS[i] - stages))


def quality_level(score: float) -> int:
    return max(0, min(4, round(score * 4)))


def trim_answer(text: str) -> str:
    text = text.strip()
    if len(text) <= ANSWER_HEAD_CHARS + ANSWER_TAIL_CHARS:
        return text
    return text[:ANSWER_HEAD_CHARS] + " … " + text[-ANSWER_TAIL_CHARS:]


class LayaRunner(Protocol):
    def predict(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]: ...


# How logs named predictions made before the runtime was recorded (Phase 2: PyTorch, stock).
STOCK_CHECKPOINT = "stock"
MIN_TIMEOUT_MS = 100.0
MAX_TIMEOUT_MS = 5000.0
TIMEOUT_MARGIN = 1.5  # the time limit is this many times the slowest measured group


def checkpoint_name(settings: Settings) -> str:
    """Names the model *and* runtime that make predictions: INT8 weights change answers, so
    comparisons with the rules only ever count predictions from exactly this setup."""
    return f"{settings.laya_model or settings.laya_checkpoint}/{settings.laya_backend}"


def cache_dir(settings: Settings) -> Path:
    base = settings.data_dir or Path.home() / ".cache" / "tempo"
    return base / "laya"


def default_loader(settings: Settings) -> Callable[[], LayaRunner]:
    def load() -> LayaRunner:
        from tempo import laya_runtime

        return laya_runtime.load(
            backend=settings.laya_backend,
            stock=settings.laya_checkpoint,
            model=settings.laya_model,
            threads=settings.laya_threads or laya_runtime.default_threads(),
            cache_dir=cache_dir(settings),
            device=settings.laya_device,
        )

    return load


def calibration_samples() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """One realistic call per group, at the lengths Tempo really sends."""
    request = (
        "Write a Python function that parses ISO-8601 durations such as P3DT4H5M into seconds, "
        "with unit tests for days, hours, minutes and invalid input. Explain the approach "
        "briefly and mention the edge cases you handle."
    )
    answer = trim_answer(
        "```python\nimport re\n\nPATTERN = re.compile(r'P(?:(\\d+)D)?(?:T(?:(\\d+)H)?"
        "(?:(\\d+)M)?(?:(\\d+)S)?)?')\n\ndef parse(text):\n    m = PATTERN.fullmatch(text)\n"
        "    if not m:\n        raise ValueError(text)\n    d, h, mi, s = (int(x or 0) for x in "
        "m.groups())\n    return ((d * 24 + h) * 60 + mi) * 60 + s\n```\n\n" + "The function "
        "matches the pattern once and converts each part to seconds. " * 12
    )
    candidates = {
        letter: f"provider/model-{i}: family {i}, strength 0.{60 + i}, good at code, math, "
        f"about {i}.5s, {1000 * i} free left"
        for i, letter in enumerate(string.ascii_uppercase[:MAX_SHORTLIST], start=1)
    }
    pick = {
        "next_model": {
            "type": "choice",
            "instructions": "Which model should do the fix step for this request?",
            "criteria": candidates,
        }
    }
    return {
        "plan": ({"request": request, "mode": "auto"}, PLAN_QUESTIONS),
        "assess": (
            {"request": request, "answer": answer, "checks": "no problems found"},
            ASSESS_QUESTIONS,
        ),
        "pick": ({"request": request, "job": "fix"}, pick),
    }


class _Worker:
    """One thread runs every Laya prediction (they already use all the CPU threads Laya is
    given). Calls an answer waits for (taken-over decisions) jump ahead of shadow ones, and
    shadow ones wait while ``hold()`` says an answer may need Laya soon, because a prediction
    that has started cannot be interrupted."""

    def __init__(self, hold: Callable[[], bool] = lambda: False) -> None:
        self._queue: queue.PriorityQueue[tuple[int, int, Callable[[], Any], Future[Any]]] = (
            queue.PriorityQueue()
        )
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._hold = hold

    def submit(self, fn: Callable[[], Any], urgent: bool) -> Future[Any]:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="laya", daemon=True)
            self._thread.start()
        future: Future[Any] = Future()
        self._queue.put((0 if urgent else 1, next(self._seq), fn, future))
        return future

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            priority, _, fn, future = item
            if priority > 0 and self._hold():
                self._queue.put(item)  # an urgent call may arrive: keep the thread free
                time.sleep(HOLD_POLL_S)
                continue
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:  # handed to whoever awaits the future
                future.set_exception(exc)


@dataclass
class LayaCall:
    status: str  # ok | off | missing | loading | error | timeout | busy | skipped | background
    answers: dict[str, Any] = field(default_factory=dict)
    ms: float | None = None
    error: str | None = None
    future: asyncio.Future[Any] | None = None  # still running after a timeout
    started: float = 0.0


class LayaDecider:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        loader: Callable[[], LayaRunner] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.settings = settings
        self.store = store
        self._loader = loader or default_loader(settings)
        self._clock = clock
        self._runner: LayaRunner | None = None
        self.status = "off" if settings.laya == "off" else "loading"
        # Which checkpoint and runtime made the predictions; comparisons only count their own.
        self.checkpoint = checkpoint_name(settings)
        # How long a prediction may take before the rules decide: set, or measured at load.
        self.timeout_ms: float = settings.laya_timeout_ms or MAX_TIMEOUT_MS
        self.calibration: dict[str, float] = {}
        self.load_error: str | None = None
        self._worker = _Worker(hold=self._hold_background)
        self._active = 0  # questions in progress
        self._pending = 0
        self._background = 0
        self._running: set[asyncio.Future[Any]] = set()
        self._cache: dict[tuple[Any, ...], tuple[LayaCall, dict[str, Any], dict[str, Any]]] = {}
        self._compare: dict[str, dict[str, Any]] = {}
        self._compare_loaded = 0.0

    # --- loading ------------------------------------------------------------------------

    def start(self) -> asyncio.Future[None] | None:
        """Load Laya in the background; questions asked meanwhile fall back to the rules."""
        if self.status == "off":
            return None
        return asyncio.wrap_future(self._worker.submit(self._load, urgent=True))

    def _load(self) -> None:
        started = time.perf_counter()
        try:
            self._runner = self._loader()
        except ImportError as exc:
            self.status, self.load_error = "missing", str(exc)
            log.warning("Laya is not installed (%s); the rules make every decision", exc)
            return
        except Exception as exc:
            self.status, self.load_error = "error", f"{type(exc).__name__}: {exc}"
            log.warning("Laya failed to load (%s); the rules make every decision", exc)
            return
        if self.settings.laya_timeout_ms is None:
            self.calibrate()
        self.status = "ready"
        log.info("Laya loaded in %.1fs", time.perf_counter() - started)

    def calibrate(self, rounds: int = 3) -> float:
        """Time each kind of call on this machine and set the time limit to the slowest one
        times TIMEOUT_MARGIN, so a CPU that is slower or faster gets a limit that fits it."""
        runner = self._runner
        assert runner is not None
        slowest: dict[str, float] = {}
        for group, (state, questions) in calibration_samples().items():
            runner.predict(state, questions)  # warm-up
            times = []
            for _ in range(rounds):
                started = time.perf_counter()
                runner.predict(state, questions)
                times.append((time.perf_counter() - started) * 1000)
            slowest[group] = max(times)
        limit = math.ceil(TIMEOUT_MARGIN * max(slowest.values()) / 10) * 10
        self.timeout_ms = float(min(MAX_TIMEOUT_MS, max(MIN_TIMEOUT_MS, limit)))
        self.calibration = {**{k: round(v, 1) for k, v in slowest.items()}}
        log.info("Laya time limit %.0f ms, measured on this machine: %s", self.timeout_ms, slowest)
        return self.timeout_ms

    def status_text(self) -> str:
        return {
            "ready": f"Laya ready ({self.checkpoint}, {self.timeout_ms:.0f} ms limit; shadow "
            "unless taken over)",
            "loading": "Laya still loading (rules decide)",
            "missing": "Laya not installed (rules decide)",
            "error": "Laya failed to load (rules decide)",
            "off": "Laya off",
        }.get(self.status, self.status)

    # --- asking -------------------------------------------------------------------------

    async def ask(
        self, state: dict[str, Any], questions: dict[str, Any], wait: bool = True
    ) -> LayaCall:
        """Ask Laya. With ``wait``, up to the time limit (the answer may be used); without, the
        prediction runs in the background and is only logged (shadow mode costs no time)."""
        if self.status != "ready" or self._runner is None:
            return LayaCall(status=self.status)
        if (wait and self._pending >= MAX_PENDING) or (
            not wait and self._background >= MAX_BACKGROUND
        ):
            return LayaCall(status="busy")
        runner = self._runner
        started = self._clock()
        future = self._worker.submit(lambda: runner.predict(state, questions), urgent=wait)
        if not wait:
            self._background += 1
            future.add_done_callback(lambda _: self._release(background=True))
            running = self._track(asyncio.wrap_future(future))
            return LayaCall(status="background", future=running, started=started)
        self._pending += 1
        future.add_done_callback(lambda _: self._release(background=False))
        waiting = self._track(asyncio.wrap_future(future))
        try:
            result = await asyncio.wait_for(asyncio.shield(waiting), timeout=self.timeout_ms / 1000)
        except TimeoutError:
            ms = (self._clock() - started) * 1000
            log.info("Laya took longer than %.0f ms; rules decide", self.timeout_ms)
            return LayaCall(status="timeout", ms=ms, future=waiting, started=started)
        except Exception as exc:
            log.warning("Laya prediction failed: %s", exc)
            return LayaCall(status="error", ms=(self._clock() - started) * 1000, error=str(exc))
        ms = (self._clock() - started) * 1000
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            return LayaCall(status="error", ms=ms, error="unexpected Laya result")
        return LayaCall(status="ok", answers=answers, ms=ms)

    def _track(self, future: asyncio.Future[Any]) -> asyncio.Future[Any]:
        self._running.add(future)
        future.add_done_callback(self._running.discard)
        return future

    async def drain(self, timeout: float = 60.0) -> None:
        """Wait for predictions still running, so their answers reach the log (tests, and
        commands such as `tempo-server collect` before they exit)."""
        if self._running:
            await asyncio.wait(list(self._running), timeout=timeout)
        for _ in range(3):  # let the done-callbacks that write the log run
            await asyncio.sleep(0)

    def question_started(self) -> None:
        self._active += 1

    def question_finished(self) -> None:
        self._active = max(0, self._active - 1)

    def _hold_background(self) -> bool:
        """Shadow predictions wait while a question that Laya decides parts of is running;
        they run between questions instead, so a waited-for call never queues behind them."""
        if self._active <= 0:
            return False
        return any(self.mode_for(name) == "laya" for name in GROUPS)

    def _release(self, background: bool) -> None:
        if background:
            self._background -= 1
        else:
            self._pending -= 1

    # --- decisions ----------------------------------------------------------------------

    def mode_for(self, name: str) -> str:
        mode = self.settings.laya_mode(name)
        if mode != "auto":
            return mode
        if time.time() - self._compare_loaded > 300 and self.store is not None:
            self._compare = self.store.laya_compare()
            self._compare_loaded = time.time()
        row = self._compare.get(name)
        if (
            row
            and (row.get("laya_model") or STOCK_CHECKPOINT) == self.checkpoint
            and row["n"] >= MIN_COMPARE_ROWS
            and row["laya_accuracy"] > row["rules_accuracy"]
        ):
            return "laya"
        return "shadow"

    async def decide(
        self, pipeline: Pipeline, name: str, stage: int, rules_value: Any, **context: Any
    ) -> Any:
        group = GROUPS[name]
        call, state, questions, mapping = await self._group(pipeline, group, stage, context, name)
        if call.status == "skipped":  # nothing to choose between
            return rules_value
        question = questions.get(name) if name != "next_model" else questions["next_model"]
        laya_value, confidence, probabilities = self._read(name, call, mapping)
        mode = self.mode_for(name)
        use_laya = (
            mode == "laya"
            and call.status == "ok"
            and laya_value is not None
            and (confidence or 0.0) >= self.settings.laya_min_confidence
        )
        value = laya_value if use_laya else rules_value
        if self.store and pipeline.e.settings.log_questions:
            row_id = self.store.add_decision(
                pipeline.qid,
                stage=stage,
                name=name,
                rules_value=json.dumps(rules_value),
                laya_value=json.dumps(laya_value) if laya_value is not None else None,
                laya_probs=probabilities,
                laya_confidence=confidence,
                laya_status=call.status,
                laya_ms=call.ms,
                used="laya" if use_laya else "rules",
                final_value=json.dumps(value),
                laya_state=state,
                laya_question=question,
                context={
                    "mode": mode,
                    **{k: v for k, v in context.items() if k != "shortlist"},
                    "mapping": mapping or None,
                    "laya_model": self.checkpoint,
                },
            )
            if call.status in ("timeout", "background") and call.future is not None:
                finished = "late" if call.status == "timeout" else "ok"
                call.future.add_done_callback(
                    lambda done, row_id=row_id, finished=finished: self._log_late(
                        done, row_id, name, mapping, call, finished
                    )
                )
        # Background (shadow) predictions land in the log later; they add no line here.
        if call.status not in ("off", "missing", "loading", "background"):
            pipeline.emit(
                "decision",
                stage=stage,
                name=name,
                value=value,
                rules=rules_value,
                laya=laya_value,
                laya_p=confidence,
                laya_status=call.status,
                laya_ms=round(call.ms, 1) if call.ms is not None else None,
                used="laya" if use_laya else "rules",
                mode=mode,
            )
        return value

    async def _group(
        self,
        pipeline: Pipeline,
        group: str,
        stage: int,
        context: dict[str, Any],
        name: str,
    ) -> tuple[LayaCall, dict[str, Any], dict[str, Any], dict[str, Any]]:
        """The Laya call that answers ``name``. Questions of this group whose decisions Laya
        has taken over are asked together and waited for; the others are asked together in the
        background, so shadow mode never slows an answer down."""
        request = pipeline.question.strip()[:REQUEST_CHARS]
        mapping: dict[str, Any] = {}
        if group == "plan":
            key: tuple[Any, ...] = (pipeline.qid, "plan")
            state = {"request": request, "mode": pipeline.o.mode}
            questions = PLAN_QUESTIONS
        elif group == "assess":
            best = pipeline.best()
            key = (pipeline.qid, "assess", stage)
            issues = best.check.issues if best is not None and best.check else []
            state = {
                "request": request,
                "answer": trim_answer(best.text if best else ""),
                "checks": "; ".join(issues[:4]) or "no problems found",
            }
            questions = ASSESS_QUESTIONS
        else:
            shortlist: list[Candidate] = list(context.get("shortlist") or [])[:MAX_SHORTLIST]
            job = context.get("job") or "draft"
            key = (pipeline.qid, "pick", stage, job, tuple(c.model.id for c in shortlist))
            letters = string.ascii_uppercase[: len(shortlist)]
            mapping = {letter: c.model.id for letter, c in zip(letters, shortlist, strict=True)}
            criteria = {
                letter: _describe_candidate(c) for letter, c in zip(letters, shortlist, strict=True)
            }
            state = {"request": request, "job": job}
            questions = {
                "next_model": {
                    "type": "choice",
                    "instructions": f"Which model should do the {job} step for this request?",
                    "criteria": criteria,
                }
            }
            if len(shortlist) < 2:
                call = LayaCall(status="skipped")
                return call, state, questions, mapping
        taken = {q for q in questions if self.mode_for(q) == "laya"}
        wait = name in taken
        subset = {q: d for q, d in questions.items() if (q in taken) == wait}
        key = (*key, "wait" if wait else "shadow")
        cached = self._cache.get(key)
        if cached is not None:
            call, state, _ = cached
            return call, state, questions, mapping
        call = await self.ask(state, subset, wait=wait)
        self._cache[key] = (call, state, subset)
        if len(self._cache) > 512:
            self._cache.pop(next(iter(self._cache)))
        return call, state, questions, mapping

    def _log_late(
        self,
        done: asyncio.Future[Any],
        row_id: int,
        name: str,
        mapping: dict[str, Any],
        call: LayaCall,
        finished: str = "late",
    ) -> None:
        """A prediction nobody waited for (or that ran past the limit) finished: record what
        Laya said, for tuning and for `tempo-server laya compare`."""
        if self.store is None or done.cancelled():
            return
        if done.exception() is not None:
            self.store.update_decision(row_id, laya_status="error")
            return
        result = done.result()
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            return
        value, confidence, probabilities = self._read(
            name, LayaCall(status="ok", answers=answers), mapping
        )
        self.store.update_decision(
            row_id,
            laya_value=json.dumps(value) if value is not None else None,
            laya_probs=probabilities,
            laya_confidence=confidence,
            laya_status=finished,
            laya_ms=(self._clock() - call.started) * 1000,
        )

    def _read(
        self, name: str, call: LayaCall, mapping: dict[str, Any]
    ) -> tuple[Any, float | None, dict[str, float] | None]:
        if call.status != "ok":
            return None, None, None
        answer = call.answers.get(name)
        if not isinstance(answer, dict):
            return None, None, None
        probabilities = answer.get("probabilities")
        confidence = answer.get("answer_confidence", answer.get("confidence"))
        if name in ("difficulty", "stage_budget", "quality"):
            levels = {int(k): float(v) for k, v in (probabilities or {}).items()}
            level = max(levels, key=levels.get) if levels else round(float(answer.get("score", 0)))
            value: Any = STAGE_BUDGET_LEVELS[level] if name == "stage_budget" else level
        elif name == "should_stop":
            value = answer.get("choice") == "A"
        elif name == "next_model":
            value = mapping.get(answer.get("choice"))
        else:
            value = answer.get("choice")
            if name == "task_type" and value not in TASKS:
                value = None
        return value, confidence, probabilities


def _describe_candidate(candidate: Candidate) -> str:
    model = candidate.model
    top = sorted(model.skills.items(), key=lambda kv: -kv[1])[:2]
    good_at = ", ".join(task for task, _ in top) or "general"
    left = candidate.quota_left.rpd if candidate.quota_left else None
    quota = "local, no quota" if left is None and model.free_rpd is None else f"{left} free left"
    return (
        f"{model.id}: {model.family} family, strength {model.strength:.2f}, good at {good_at}, "
        f"about {candidate.latency_s:.1f}s, {quota}"
    )
