"""Laya as the fast decision-maker at every stage.

Laya (convaiinnovations/laya, Apache-2.0, ``pip install laya``) answers typed questions
(choice / score / yes-no as a two-option choice) about a text with calibrated probabilities in
one forward pass. It does not generate text. Tempo asks it three groups of questions:

    plan    before stage 1: task type, difficulty, strategy, stage budget
    assess  after each check: answer quality, stop or continue
    pick    before a stage: which model from a shortlist of at most 10

Every decision starts in shadow mode: Laya predicts, the rules decide, and both are logged so
Laya can be fine-tuned on Tempo's own outcomes. TEMPO_LAYA_TAKEOVER hands a decision to Laya
("laya"), or to Laya only once `tempo laya compare` shows it beats the rules on held-out data
("auto"). If Laya is missing, errors, is busy, or takes longer than the timeout (200 ms by
default), the rules decide and the reason is logged.

Laya runs in-process on one worker thread (``laya.Router(preload=True)``), never through
laya-serve, whose default port 8000 is Tempo's own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import string
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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


# Laya's Router slot that holds a fine-tuned checkpoint (TEMPO_LAYA_MODEL).
TUNED_SLOT = "typed-decisions"
# How logs name Laya's own checkpoints, as opposed to a TEMPO_LAYA_MODEL path or repo.
STOCK_CHECKPOINT = "stock"


@dataclass
class PinnedRunner:
    """Sends every prediction to one checkpoint instead of Laya's language routing."""

    router: Any
    model: str

    def predict(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        return self.router.predict(state, questions, model=self.model)


def default_loader(settings: Settings) -> Callable[[], LayaRunner]:
    def load() -> LayaRunner:
        from laya import Router  # optional dependency: pip install "tempo-server[laya]"

        kwargs: dict[str, Any] = {}
        if settings.laya_device:
            kwargs["device"] = settings.laya_device
        if not settings.laya_model:
            return Router(preload=True, **kwargs)
        # A checkpoint fine-tuned on `tempo export-laya` data answers every decision, in any
        # language, and is the only one loaded.
        router = Router(models={TUNED_SLOT: settings.laya_model}, **kwargs)
        router.preload([TUNED_SLOT])
        return PinnedRunner(router, TUNED_SLOT)

    return load


@dataclass
class LayaCall:
    status: str  # ok | off | missing | loading | error | timeout | busy | skipped
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
        # Which checkpoint made the predictions; comparisons only count its own.
        self.checkpoint = settings.laya_model or STOCK_CHECKPOINT
        self.load_error: str | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya")
        self._pending = 0
        self._cache: dict[tuple[Any, ...], tuple[LayaCall, dict[str, Any], dict[str, Any]]] = {}
        self._compare: dict[str, dict[str, Any]] = {}
        self._compare_loaded = 0.0

    # --- loading ------------------------------------------------------------------------

    def start(self) -> asyncio.Future[None] | None:
        """Load Laya in the background; questions asked meanwhile fall back to the rules."""
        if self.status == "off":
            return None
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(self._executor, self._load)

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
        self.status = "ready"
        log.info("Laya loaded in %.1fs", time.perf_counter() - started)

    def status_text(self) -> str:
        return {
            "ready": "Laya ready (shadow unless taken over)",
            "loading": "Laya still loading (rules decide)",
            "missing": "Laya not installed (rules decide)",
            "error": "Laya failed to load (rules decide)",
            "off": "Laya off",
        }.get(self.status, self.status)

    # --- asking -------------------------------------------------------------------------

    async def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> LayaCall:
        if self.status != "ready" or self._runner is None:
            return LayaCall(status=self.status)
        if self._pending >= MAX_PENDING:
            return LayaCall(status="busy")
        self._pending += 1
        loop = asyncio.get_running_loop()
        runner = self._runner
        started = self._clock()
        future = loop.run_in_executor(self._executor, lambda: runner.predict(state, questions))

        def release(_: Any) -> None:
            self._pending -= 1

        future.add_done_callback(release)
        timeout = self.settings.laya_timeout_ms / 1000
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError:
            ms = (self._clock() - started) * 1000
            log.info("Laya took longer than %.0f ms; rules decide", self.settings.laya_timeout_ms)
            return LayaCall(status="timeout", ms=ms, future=future, started=started)
        except Exception as exc:
            log.warning("Laya prediction failed: %s", exc)
            return LayaCall(status="error", ms=(self._clock() - started) * 1000, error=str(exc))
        ms = (self._clock() - started) * 1000
        answers = result.get("answers") if isinstance(result, dict) else None
        if not isinstance(answers, dict):
            return LayaCall(status="error", ms=ms, error="unexpected Laya result")
        return LayaCall(status="ok", answers=answers, ms=ms)

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
        call, state, questions, mapping = await self._group(pipeline, group, stage, context)
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
            if call.status == "timeout" and call.future is not None:
                call.future.add_done_callback(
                    lambda done, row_id=row_id: self._log_late(done, row_id, name, mapping, call)
                )
        if call.status not in ("off", "missing", "loading"):
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
        self, pipeline: Pipeline, group: str, stage: int, context: dict[str, Any]
    ) -> tuple[LayaCall, dict[str, Any], dict[str, Any], dict[str, Any]]:
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
        cached = self._cache.get(key)
        if cached is not None:
            call, state, _ = cached
            return call, state, questions, mapping
        call = await self.ask(state, questions)
        self._cache[key] = (call, state, questions)
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
    ) -> None:
        """A timed-out prediction finished: record what Laya would have said (for tuning)."""
        if self.store is None or done.cancelled() or done.exception() is not None:
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
            laya_status="late",
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
