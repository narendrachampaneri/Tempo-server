"""The staged engine. Each question runs as a sequence of stages, each with one job:

    draft  -> one or more models answer (in parallel for a mixture); the first draft streams
    check  -> heuristics plus an optional LLM judge grade every new answer
    fix    -> a stronger model rewrites the best answer, fixing the issues found
    merge  -> an aggregator combines the strongest answers into one
    polish -> a last-stage rewrite when no stage is left for another check
    split / parts / combine -> multi-part requests are split, answered part by part, combined

It stops as soon as an answer passes its check, or when the stage, time or free-quota budget
for the question runs out, and then returns the best answer so far. Later stages replace the
streamed draft live (answer_reset, then new deltas); answer_final always carries the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tempo import compat, execute, prompts
from tempo.analyzer import analyze, message_text
from tempo.checks import (
    PASS_THRESHOLD,
    CheckResult,
    combine,
    extract_json,
    parse_judge,
    quick_checks,
    run_heuristics,
)
from tempo.events import Event
from tempo.laya_decider import DIFFICULTY_COMPLEXITY, complexity_level, quality_level
from tempo.providers import ERROR_LABELS, ProviderError
from tempo.router import TOO_SLOW, Candidate
from tempo.speed import estimate_seconds
from tempo.types import ModelInfo, QueryProfile

if TYPE_CHECKING:
    from tempo.engine import Engine, RunOptions

log = logging.getLogger(__name__)

STRATEGIES = ("single", "cascade", "mixture", "decompose")
_END = object()


class BudgetStop(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class StreamBroken(Exception):
    """A live answer failed mid-stream and the client cannot take a replacement."""


@dataclass
class Answer:
    text: str
    model: str
    stage: int
    job: str
    reasoning: str = ""
    finish_reason: str | None = None
    check: CheckResult | None = None
    call_id: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.check.score if self.check else -1.0


@dataclass
class Plan:
    strategy: str
    stage_budget: int
    drafts: int
    parts: list[str] | None = None
    reasons: dict[str, str] = field(default_factory=dict)


class Pipeline:
    def __init__(
        self,
        engine: Engine,
        messages: Sequence[dict[str, Any]],
        options: RunOptions,
        question_id: str,
    ) -> None:
        self.e = engine
        self.o = options
        self.messages = [dict(m) for m in messages]
        self.qid = question_id
        self.clock = engine._clock
        self.t0 = self.clock()
        self.deadline = self.t0 + options.time_budget_s
        self.question = prompts.last_user_text(self.messages)

        self.profile: QueryProfile | None = None
        self.plan: Plan | None = None
        self.stage = 0
        self.max_stages = options.max_stages
        self.requests = 0  # provider requests (local models are free and not counted)
        self.calls = 0
        self.answers: list[Answer] = []
        self.jobs_done: list[str] = []
        self.stop_reason = "passed"
        self.final: Answer | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._stage_t0 = 0.0
        self._stage_models: list[str] = []
        self._live_owner: tuple[int, int] | None = None
        self._shown = False
        # The time budget stops new stages, never an answer that is already arriving: calls
        # whose answer text has started (stage, slot) are finished, and what each call has
        # received so far is kept in case even the grace period runs out.
        self._time_up = False
        self._time_capped = False  # the plan dropped stages that couldn't fit the time budget
        # The first good answer is shown as soon as it arrives ("answer_ready"); checking goes
        # on in the background and a better answer replaces it with "answer_revised".
        self._shown_answer: Answer | None = None
        self._started: set[tuple[int, int]] = set()
        self._partial: dict[tuple[int, int], tuple[str, str]] = {}
        # OpenAI features: tool calling and strict JSON (validated per reply; tempo/compat.py).
        self.tool_req = (
            compat.ToolRequest(
                options.tools,
                options.tool_choice if options.tool_choice is not None else "auto",
                options.parallel_tool_calls is not False,
            )
            if options.tools
            else None
        )
        self.json_fmt = compat.JsonFormat.from_request(options.response_format)
        self._last_invalid: str | None = None
        self._math_code: tuple[str | None, str] | None = None  # sandbox maths, once per question
        if self.tool_req or self.json_fmt:
            options.live = False  # validated before anything is shown

    # --- event plumbing ------------------------------------------------------------

    def emit(self, event_type: str, /, **data: Any) -> None:
        assert self._queue is not None
        self._queue.put_nowait(Event(event_type, round(self.clock() - self.t0, 3), data))

    async def events(self) -> AsyncIterator[Event]:
        self._queue = asyncio.Queue()
        task = asyncio.create_task(self._run())
        try:
            while True:
                item = await self._queue.get()
                if item is _END:
                    break
                yield item
            await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _run(self) -> None:
        assert self._queue is not None
        try:
            await self._main()
        except asyncio.CancelledError:
            raise
        except StreamBroken:
            pass  # already reported
        except Exception as exc:  # never leave a client hanging
            log.exception("pipeline failed")
            self.emit("error", message=f"Internal error ({type(exc).__name__}).", kind="internal")
            self._log(error=f"{type(exc).__name__}: {exc}")
        finally:
            self._queue.put_nowait(_END)

    # --- budgets -------------------------------------------------------------------

    def time_left(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def stages_left(self) -> int:
        return max(0, self.max_stages - self.stage)

    def requests_left(self) -> int:
        return max(0, self.o.quota_budget - self.requests)

    async def _bounded(
        self,
        calls: Sequence[Any],
        *,
        stage: int,
        job: str,
        finish_started: bool = True,
    ) -> list[Answer | None]:
        """Run one stage's model calls (slot i is ``calls[i]``) within the time left.

        When the time budget ends: finished answers are kept; calls whose answer text is
        already arriving are finished (``finish_started``, for answers; at most
        TEMPO_FINISH_GRACE seconds more); calls that have not started are cancelled. No new
        stage starts afterwards (``_need_stage``).
        """
        tasks = [asyncio.ensure_future(call) for call in calls]
        results: list[Answer | None] = [None] * len(tasks)
        try:
            _, pending = await asyncio.wait(tasks, timeout=self.time_left() or 0.001)
            if pending:
                self._time_up = True
                keep = []
                for slot, task in enumerate(tasks):
                    if task.done():
                        continue
                    if finish_started and (stage, slot) in self._started:
                        keep.append(task)
                    else:
                        task.cancel()
                if keep:
                    self.emit(
                        "note",
                        message="Time budget reached while an answer was arriving: finishing it "
                        "(no new stage starts).",
                    )
                    await asyncio.wait(keep, timeout=self.e.settings.finish_grace_s)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for slot, task in enumerate(tasks):
            if task.cancelled():
                results[slot] = self._salvage(stage, job, slot)
                continue
            error = task.exception()
            if error is not None:
                raise error
            results[slot] = task.result()
        return results

    def _salvage(self, stage: int, job: str, slot: int) -> Answer | None:
        """An answer that was still arriving when even the grace period ran out: keep what
        came (marked as cut, so the checks and the note say so)."""
        partial = self._partial.get((stage, slot))
        if partial is None or not partial[1].strip() or job not in ANSWER_JOBS | {"parts"}:
            return None
        model_id, text = partial
        self.emit(
            "note",
            message=f"{model_id} was still writing after the {self.e.settings.finish_grace_s:g}s "
            "grace period: keeping what it wrote so far.",
        )
        return Answer(text=text, model=model_id, stage=stage, job=job, finish_reason="length")

    def _stages_used(self) -> BudgetStop:
        """No stage left. When the plan had to drop stages to fit the time budget, that is
        the time budget speaking, and the note says so."""
        if self._time_capped:
            return BudgetStop(
                "time",
                f"the {self.o.time_budget_s:g}s time budget leaves no time for another stage",
            )
        return BudgetStop("stages", f"stage budget of {self.max_stages} used")

    def _need_stage(self, job: str) -> None:
        if self.stages_left() <= 0:
            raise self._stages_used()
        if self._time_up or self.time_left() <= 0:
            raise BudgetStop("time", f"time budget of {self.o.time_budget_s:g}s reached")

    # --- main flow -----------------------------------------------------------------

    async def _main(self) -> None:
        e, o = self.e, self.o
        self.emit("received", mode=o.mode, question_id=self.qid)
        if not any(m.get("role") == "user" for m in self.messages):
            self.emit("error", message="There is no user message to answer.", kind="bad_request")
            return
        self._log_start()

        if await self._try_cache():
            return

        self.profile, source = await e.understand(self.messages)
        p = self.profile
        task = await e.decide(self, "task_type", 0, p.task)
        rules_level = complexity_level(p.complexity)
        level = await e.decide(self, "difficulty", 0, rules_level)
        if task != p.task or level != rules_level:
            update: dict[str, Any] = {"task": task}
            if level != rules_level:
                update["complexity"] = DIFFICULTY_COMPLEXITY[level]
            self.profile = p = p.model_copy(update=update)
            source = f"{source} + Laya"
        self.emit(
            "analyze",
            task=p.task,
            complexity=p.complexity,
            script=p.script,
            needs=p.needs,
            input_tokens=p.input_tokens,
            est_output_tokens=p.est_output_tokens,
            source=source,
        )

        if o.model:
            requested = e.registry.get(o.model)
            if requested is None:
                self.emit("error", message=f"Unknown model: {o.model}", kind="not_found")
                self._log(error="unknown model")
                return
            reason = e.router.skip_reason(
                requested,
                p,
                local_only=o.local_only or o.mode == "private",
                allow_providers=o.allow_providers,
                access=o.access,
                no_logging=o.no_logging,
                training_only=o.training_only,
                explicit=True,
                exclude_families=o.exclude_families or (),
            )
            if reason:
                message = f"{o.model} is unavailable: {reason}"
                self.emit("error", message=message, kind="unavailable")
                self._log(error="requested model unavailable")
                return

        route = self._rank("draft", fit=False)
        self._note_quota_fallback(route)
        if not route.candidates:
            self.emit(
                "error",
                message=e.no_model_message(route),
                kind="unavailable",
                skipped=route.skipped_summary(),
            )
            self._log(error="no model available")
            return

        self.plan = await self._make_plan()
        self.max_stages = min(o.max_stages, self.plan.stage_budget)
        estimates = self._stage_estimates()
        fits = self._stages_that_fit(estimates)
        if fits < self.max_stages:
            self.max_stages = fits
            self._time_capped = True
        self.emit(
            "plan",
            strategy=self.plan.strategy,
            estimates={k: round(v, 1) for k, v in estimates.items()},
            fits=fits,
            est_output_tokens=p.est_output_tokens,
            max_stages=self.max_stages,
            drafts=self.plan.drafts,
            parts=len(self.plan.parts or []),
            time_budget_s=o.time_budget_s,
            quota_budget=o.quota_budget,
            reason=self.plan.reasons.get("strategy", ""),
            laya=e.laya.status_text() if e.laya is not None else None,
        )

        try:
            if self.plan.strategy == "tools":
                await self._tools()
            else:
                if self.plan.strategy == "decompose":
                    await self._decompose()
                else:
                    await self._draft(self.plan.drafts)
                await self._improve()
        except BudgetStop as stop:
            self.stop_reason = f"budget_{stop.reason}"
            self.emit(
                "budget", reason=stop.reason, detail=stop.detail, has_answer=bool(self.answers)
            )
        except _NoAnswer as failure:
            # Models answered, but no reply was a valid tool call or JSON: "invalid" (502).
            kind = "invalid" if self._last_invalid else failure.kind
            self.emit("error", message=str(failure), kind=kind, tried=failure.tried)
            self._log(error=str(failure))
            return
        self._finish()

    async def _try_cache(self) -> bool:
        e, o = self.e, self.o
        if not (e.cache and o.use_cache and self._cacheable()):
            return False
        hit = await e.cache.lookup(self.question, o.mode, o.access.user_id)
        if hit is None:
            return False
        self.emit(
            "cache_hit",
            model=hit.model,
            similarity=round(hit.similarity, 3),
            age_s=round(hit.age_s),
        )
        self.final = Answer(hit.answer, hit.model, 0, "cache")
        self.stop_reason = "cache"
        self.emit(
            "answer_final",
            answer=hit.answer,
            model=hit.model,
            stage=0,
            score=None,
            passed=True,
            cached=True,
        )
        self._done()
        return True

    def _cacheable(self) -> bool:
        if self.tool_req or self.json_fmt:
            return False
        turns = [m for m in self.messages if m.get("role") in ("user", "assistant")]
        return (
            len(turns) == 1
            and self.o.model is None
            and not self.o.allow_providers
            and not self.o.exclude_families
        )

    # --- planning ------------------------------------------------------------------

    async def _make_plan(self) -> Plan:
        o, p = self.o, self.profile
        assert p is not None
        parts = prompts.rule_split(self.question)
        if self.tool_req:
            # One validated tool stage; the rest of the budget is for checking a text answer.
            return Plan(
                "tools", o.max_stages, 1, reasons={"strategy": "tool calling: one validated call"}
            )
        if self.json_fmt:
            parts = None  # one JSON answer: no splitting into parts
        if o.strategy in STRATEGIES:
            strategy, why = o.strategy, "requested"
        elif o.max_stages == 1:
            strategy, why = "single", "one stage allowed"
        elif parts:
            strategy, why = "decompose", f"{len(parts)} separate tasks in the request"
        elif o.mode == "best" or p.complexity >= 0.7:
            strategy, why = "mixture", "hard question or best mode: several models, then merge"
        elif p.complexity >= 0.35 or p.task in ("code", "math", "reasoning"):
            strategy, why = "cascade", "draft, check, then fix if needed"
        else:
            strategy, why = "single", "simple question: one draft and a quick check"
        strategy = await self.e.decide(self, "strategy", 0, strategy)
        budget = {"single": 3, "cascade": o.max_stages, "mixture": o.max_stages}.get(
            strategy, o.max_stages
        )
        budget = await self.e.decide(self, "stage_budget", 0, min(o.max_stages, budget))
        if self.json_fmt and strategy in ("decompose", "mixture"):
            strategy = "cascade"
        drafts = min(o.max_parallel, 3) if strategy == "mixture" else 1
        if strategy == "decompose" and not parts:
            parts = None  # the split stage will ask a model
        return Plan(
            strategy=strategy,
            stage_budget=max(1, min(o.max_stages, int(budget))),
            drafts=max(1, drafts),
            parts=parts if strategy == "decompose" else None,
            reasons={"strategy": why},
        )

    def _stage_estimates(self) -> dict[str, float]:
        """Seconds each kind of stage is expected to take with the model the router would pick
        now: from measured speed, the answer's expected length and the models' output limits."""
        out: dict[str, float] = {}
        for job, tokens in (
            ("draft", self._job_tokens("draft")),
            ("check", self._job_tokens("check")),
            ("fix", self._job_tokens("fix")),
        ):
            ranked = self._rank(job, fit=False).candidates
            if job == "check" and not self._want_judge():
                out[job] = 0.0  # heuristics only
            elif ranked:
                out[job] = estimate_seconds(ranked[0].model, tokens)
        return out

    def _stages_that_fit(self, estimates: dict[str, float]) -> int:
        """Stages (draft, then check and fix in turn) that fit the time budget. At least two
        when two are allowed: a check with the heuristics alone takes no time."""
        budget = self.o.time_budget_s
        spent = estimates.get("draft", 0.0)
        stages = 1
        while stages < self.max_stages:
            job = "check" if stages % 2 else "fix"
            if spent + estimates.get(job, 0.0) > budget:
                break
            spent += estimates.get(job, 0.0)
            stages += 1
        return max(stages, min(2, self.max_stages))

    # --- ranking and model choice ----------------------------------------------------

    def _rank(
        self,
        job: str,
        profile: QueryProfile | None = None,
        exclude: Sequence[str] = (),
        fit: bool = True,
    ):
        """Models for this job, best first. ``fit``: only models that can finish it in the
        time left, by their measured speed."""
        o = self.o
        p = profile or self.profile
        assert p is not None
        mode = o.mode
        if job in ("fix", "merge", "polish", "combine"):
            # Improvement jobs want strength: pretend the question is harder than it looked.
            p = p.model_copy(update={"complexity": min(1.0, p.complexity + 0.3)})
            mode = "best" if o.mode in ("best", "auto") else o.mode
        elif job in ("check", "split"):
            p = p.model_copy(update={"est_output_tokens": 200})
            mode = "private" if o.mode == "private" else "fast"
        return self.e.router.rank(
            p,
            mode=mode,
            allow_providers=o.allow_providers,
            local_only=o.local_only,
            access=o.access,
            exclude=exclude,
            reserve=o.quota_reserve,
            no_logging=o.no_logging,
            training_only=o.training_only,
            exclude_families=o.exclude_families or (),
            time_left_s=self.time_left() if fit else None,
        )

    def _job_tokens(self, job: str) -> int:
        """Output tokens a job's reply is expected to have."""
        if job in ("check", "split", "compute"):
            return 200
        return self.profile.est_output_tokens if self.profile else 500

    def _too_slow(self, route: Any) -> bool:
        """Every model left was skipped only because it can't finish in the time left."""
        return not route.candidates and TOO_SLOW in route.skipped

    def _slots(self, ranked: list[Candidate], count: int) -> list[list[Candidate]]:
        """``count`` fallback lists whose first choices come from different model families."""
        if not ranked:
            return []
        primaries: list[Candidate] = []
        for candidate in ranked:
            if len(primaries) == count:
                break
            if all(candidate.model.family != p.model.family for p in primaries):
                primaries.append(candidate)
        for candidate in ranked:  # not enough families: fill with other models
            if len(primaries) == count:
                break
            if candidate not in primaries:
                primaries.append(candidate)
        others = [c for c in ranked if c not in primaries]
        return [[primary, *others][: self.o.max_attempts] for primary in primaries]

    async def _pick(self, job: str, ranked: list[Candidate], count: int = 1):
        slots = self._slots(ranked, count)
        if slots:
            first_id = await self.e.decide(
                self,
                "next_model",
                self.stage + 1,
                slots[0][0].model.id,
                shortlist=ranked[:10],
                job=job,
            )
            if first_id != slots[0][0].model.id:
                chosen = next(c for c in ranked if c.model.id == first_id)
                rest = [c for c in ranked if c is not chosen]
                slots[0] = [chosen, *rest][: self.o.max_attempts]
        return slots

    # --- stages ----------------------------------------------------------------------

    def _begin(self, job: str, models: list[str], reason: str) -> int:
        self._need_stage(job)
        self.stage += 1
        self._stage_t0 = self.clock()
        self._stage_models = list(models)
        self.emit(
            "stage_start",
            stage=self.stage,
            max_stages=self.max_stages,
            job=job,
            models=models,
            reason=reason,
            requests_left=self.requests_left(),
            time_left_s=round(self.time_left(), 1),
        )
        return self.stage

    def _end(self, stage: int, job: str, outputs: list[Answer], check: Any = None) -> None:
        ms = round((self.clock() - self._stage_t0) * 1000)
        quota: dict[str, Any] = {}
        for model_id in dict.fromkeys(self._stage_models + [a.model for a in outputs]):
            model = self.e.registry.get(model_id)
            if model is not None and self.e.quota is not None:
                key_id = self.o.access.key_id(model.provider)
                quota[model_id] = self.e.quota.left(model, key_id).as_dict()
        self.jobs_done.append(job)
        self.emit(
            "stage_end",
            stage=stage,
            job=job,
            ms=ms,
            answers=len(outputs),
            requests_used=self.requests,
            requests_left=self.requests_left(),
            time_left_s=round(self.time_left(), 1),
            quota=quota,
        )
        if self._logging:
            self.e.store.add_stage(
                self.qid,
                idx=stage,
                job=job,
                models=self._stage_models,
                outputs=[
                    {"model": a.model, "text": a.text, "reasoning": a.reasoning} for a in outputs
                ],
                check_result=check,
                ms=ms,
                requests=self.requests,
                quota_left=quota,
            )

    async def _draft(self, count: int, exclude_families: set[str] | None = None) -> None:
        route = self._rank("draft")
        ranked = route.candidates
        if self._too_slow(route):
            if self.answers:
                raise BudgetStop("time", "no model can write another draft in the time left")
            # Nothing is expected to finish in time and there is no answer yet: the fastest
            # model is still better than no answer.
            ranked = sorted(self._rank("draft", fit=False).candidates, key=lambda c: c.latency_s)
            if ranked:
                self.emit(
                    "note",
                    message=f"No model is expected to finish within the {self.time_left():.0f}s "
                    f"left; trying the fastest, {ranked[0].model.id} "
                    f"(~{ranked[0].latency_s:.0f}s).",
                )
        if exclude_families:
            fresh = [c for c in ranked if c.model.family not in exclude_families]
            ranked = fresh or ranked
        if self.o.model and not self.answers:
            requested = self.e.registry.get(self.o.model)
            if requested is not None:
                profile, mode, access = self.profile, self.o.mode, self.o.access
                chosen = self.e.router.candidate(requested, profile, mode, access)
                chosen.why = "requested by caller"
                ranked = [chosen, *[c for c in ranked if c.model.id != requested.id]]
        ranked = self._local_first(ranked)
        count = self._affordable(count, ranked)
        slots = await self._pick("draft", ranked, count)
        if not slots:
            raise BudgetStop("quota", "no model left within the free-quota budget")
        job = "draft"
        reason = slots[0][0].why if count == 1 else f"{count} models from different families"
        stage = self._begin(job, [s[0].model.id for s in slots], reason)
        messages = prompts.draft_messages(self.messages, self.o.system_prompt)
        results = await self._bounded(
            [
                self._call(stage, job, slot, messages, live=True, slot_id=i)
                for i, slot in enumerate(slots)
            ],
            stage=stage,
            job=job,
        )
        outputs = [r for r in results if r is not None]
        self._end(stage, job, outputs)
        if not outputs:
            kind = "budget" if self._time_up else "unavailable"
            raise _NoAnswer(
                self._failure_message(), [c.model.id for s in slots for c in s], kind=kind
            )
        self.answers.extend(outputs)
        self._maybe_ready(stage, results)

    def _note_quota_fallback(self, route: Any) -> None:
        """When every free quota is used up, say so: local models (and the cache, already
        checked) answer instead."""
        used_up = [
            m
            for reason, ids in route.skipped.items()
            if "used up" in reason or "kept for users" in reason
            for m in ids
        ]
        hosted_left = [
            c for c in route.candidates if not self.e.registry.providers[c.model.provider].local
        ]
        if not used_up or hosted_left:
            return
        if route.candidates:
            self.emit(
                "note",
                message="Free quota is used up on every provider: answering with local model "
                f"{route.candidates[0].model.id} (the cache was checked first).",
            )
        else:
            self.emit(
                "note",
                message="Free quota is used up on every provider and no local model is running "
                "(the cache was checked first).",
            )

    def _local_first(self, ranked: list[Candidate]) -> list[Candidate]:
        """Simple questions go to a local model first when Ollama is running with a model
        installed (TEMPO_LOCAL_FIRST=auto, the default; off turns it off)."""
        s, p = self.e.settings, self.profile
        if s.local_first == "off" or p is None or self.o.mode in ("best", "private"):
            return ranked
        if self.o.model or self.tool_req or self.json_fmt or self.answers:
            return ranked
        if p.complexity > s.local_first_max_complexity:
            return ranked
        # Only a model Ollama reported as installed: Ollama is running and has it.
        local = [
            c
            for c in ranked
            if self.e.registry.providers[c.model.provider].local and c.model.installed is True
        ]
        if not local or local[0] is ranked[0]:
            return ranked
        best = local[0]
        best.why = "simple question: a local model saves free quota"
        self.emit(
            "note",
            message=f"Local first: a simple question, so local model {best.model.id} answers "
            "and your free quota is saved (turn off with TEMPO_LOCAL_FIRST=off).",
        )
        return [best, *[c for c in ranked if c is not best]]

    async def _tools(self) -> None:
        """Tool calling: one model reply, validated (functions exist, arguments match their
        schemas, a required call is there); a reply that fails is retried on the next model."""
        ranked = self._rank("draft").candidates
        if self.o.model:
            requested = self.e.registry.get(self.o.model)
            if requested is not None:
                chosen = self.e.router.candidate(
                    requested, self.profile, self.o.mode, self.o.access
                )
                chosen.why = "requested by caller"
                ranked = [chosen, *[c for c in ranked if c.model.id != requested.id]]
        slots = await self._pick("draft", ranked, 1)
        if not slots:
            raise BudgetStop("quota", "no model left within the free-quota budget")
        stage = self._begin("tools", [slots[0][0].model.id], slots[0][0].why)
        messages = prompts.draft_messages(self.messages, self.o.system_prompt)
        (result,) = await self._bounded(
            [self._call(stage, "tools", slots[0], messages, live=False)], stage=stage, job="tools"
        )
        self._end(stage, "tools", [result] if result else [])
        if result is None:
            kind = "budget" if self._time_up else "unavailable"
            raise _NoAnswer(self._failure_message(), [c.model.id for c in slots[0]], kind=kind)
        self.answers.append(result)
        if not result.tool_calls and await self._tool_followup(result):
            return  # the text answer went through the judge and fix stages
        result.check = CheckResult(score=1.0, passed=True)
        self.emit(
            "check",
            stage=stage,
            results=[{"model": result.model, "stage": stage, **result.check.as_dict()}],
            judge_model=None,
            best_score=1.0,
            passed=True,
        )
        self.final = result
        self.stop_reason = "passed"

    async def _tool_followup(self, result: Answer) -> bool:
        """A text answer to a tool-calling request (TEMPO_TOOL_FOLLOWUP). Returns True when it
        was handed to the judge and fix stages."""
        setting = self.e.settings.tool_followup
        if setting == "off":
            return False
        failed = quick_checks(self.question, result.text)
        if setting == "full" or self.o.mode == "best" or failed.hard_fail:
            if failed.hard_fail:
                self.emit(
                    "note",
                    message="Quick check failed on the answer after the tool result: "
                    + "; ".join(failed.issues)
                    + " · sending it to the judge and fix stages",
                )
            await self._improve()
            return True
        return False

    def _prepare(self, job: str, model: ModelInfo, messages: list[dict[str, Any]]):
        """Per model: the messages and extra arguments for this call (native or emulated tools,
        the JSON schema instruction)."""
        extra: dict[str, Any] = {}
        if self.tool_req and job == "tools":
            if model.tools:  # native tool calls; everyone else gets them described in text
                extra = {
                    "tools": self.tool_req.tools,
                    "tool_choice": self.o.tool_choice,
                    "parallel_tool_calls": self.o.parallel_tool_calls,
                }
            else:
                messages = compat.emulated_messages(messages, self.tool_req)
        if self.json_fmt and job in ANSWER_JOBS:
            messages = _with_system(messages, compat.json_system_prompt(self.json_fmt))
            if model.structured_outputs and self.o.response_format:
                # Native structured output where the provider supports it; the answer is
                # still validated like every other.
                extra["response_format"] = self.o.response_format
        if self.tool_req and job != "tools":
            messages = compat.flatten_tool_turns(messages)
        if self.tool_req or self.json_fmt:
            messages = _merge_systems(messages)
        return messages, extra

    def _validate(self, job: str, text: str, native: list[dict[str, Any]]) -> compat.CallOutcome:
        """Is this reply usable? Tool calls parsed and checked; strict JSON checked."""
        out = compat.CallOutcome(text=text)
        if self.tool_req and job == "tools":
            calls = [dict(c, id=c.get("id") or compat.new_call_id()) for c in native]
            rest = text
            if not calls:
                calls, rest = compat.parse_text_tool_calls(text, set(self.tool_req.by_name))
            out.tool_calls, out.text = calls, rest
            out.issues = compat.validate_tool_calls(calls, self.tool_req)
            if not calls and not rest.strip() and not out.issues:
                out.issues = ["the model returned neither text nor a tool call"]
        elif self.json_fmt and job in ANSWER_JOBS:
            out.issues, out.text = compat.validate_json_answer(text, self.json_fmt)
        return out

    def _affordable(self, count: int, ranked: list[Candidate]) -> int:
        """Parallel slots the free-quota budget can pay for (local models are free)."""
        local = sum(1 for c in ranked if self.e.registry.providers[c.model.provider].local)
        return max(1, min(count, max(self.requests_left(), min(local, count))))

    def _pending(self) -> list[Answer]:
        return [a for a in self.answers if a.check is None]

    def _want_judge(self) -> bool:
        if not self.e.settings.judge:
            return False
        if self.requests_left() <= 0 and not self._local_available():
            return False
        p = self.profile
        assert p is not None
        if self.o.mode == "best":
            return True
        if self.o.mode == "fast":
            return p.complexity >= 0.5
        return p.complexity >= 0.3 or p.task in ("code", "math", "reasoning", "extract")

    def _local_available(self) -> bool:
        return any(
            self.e.registry.providers[c.model.provider].local
            for c in self._rank("check").candidates
        )

    async def _check(self) -> None:
        pending = self._pending()
        if not pending:
            return
        p = self.profile
        assert p is not None
        structured = self.json_fmt is not None
        heur = {
            id(a): run_heuristics(p, self.question, a.text, a.finish_reason, structured)
            for a in pending
        }
        gradable = [a for a in pending if not heur[id(a)].hard_fail]
        will_run = self._runnable(pending)
        judge_slot: list[Candidate] = []
        if gradable and self._want_judge():
            families = {self._family(a.model) for a in gradable}
            ranked = self._rank("check", exclude=[a.model for a in gradable]).candidates
            # Previews, last-resort routers and providers kept out of judging never judge.
            ranked = [
                c
                for c in ranked
                if not (c.model.preview or c.model.fallback_only)
                and not self.e.registry.blocked_for(c.model.provider, "judge")
            ]
            other = [c for c in ranked if c.model.family not in families]
            judge_ranked = other or ranked
            if self.requests_left() <= 0:
                judge_ranked = [
                    c for c in judge_ranked if self.e.registry.providers[c.model.provider].local
                ]
            slots = await self._pick("check", judge_ranked)
            judge_slot = slots[0] if slots else []
        reason = f"heuristics + judge {judge_slot[0].model.id}" if judge_slot else "heuristics only"
        if will_run:
            reason = f"{reason} + sandbox"
        stage = self._begin("check", [judge_slot[0].model.id] if judge_slot else [], reason)
        if will_run:
            await self._execute(stage, will_run, heur)
            gradable = [a for a in gradable if not heur[id(a)].hard_fail]
        grades = None
        judge_model = None
        if judge_slot and gradable:
            messages = prompts.judge_messages(self.messages, [a.text for a in gradable])
            (reply,) = await self._bounded(
                [self._call(stage, "check", judge_slot, messages, live=False)],
                stage=stage,
                job="check",
                finish_started=False,
            )
            if reply is not None:
                grades = parse_judge(reply.text, len(gradable))
                judge_model = reply.model if grades else None
        graded = {id(a): grades[i] for i, a in enumerate(gradable)} if grades else {}
        for answer in pending:
            grade = graded.get(id(answer))
            answer.check = combine(
                heur[id(answer)],
                self.o.mode,
                grade.score if grade else None,
                grade.issues if grade else None,
                judge_model if grade else None,
            )
            if grade and answer.call_id and self.e.store:
                self.e.store.set_call_score(answer.call_id, answer.check.score)
        results = [
            {"model": a.model, "stage": a.stage, **a.check.as_dict()}
            for a in pending
            if a.check is not None
        ]
        best = self.best()
        self.emit(
            "check",
            stage=stage,
            results=results,
            judge_model=judge_model,
            best_score=best.score if best else None,
            passed=bool(best and best.check and best.check.passed),
        )
        self._end(stage, "check", [], check=results)
        if best is not None and best.check is not None and self._shown_answer is not None:
            shown = self._shown_answer
            if best is not shown and best.score > shown.score:
                self._revise(best)

    # --- running code and maths in the sandbox (tempo/execute.py) ----------------------------

    def _runnable(self, pending: list[Answer]) -> list[tuple[Answer, str, Any]]:
        """Answers the sandbox can check: (answer, "code" | "math", the program or None)."""
        sandbox, p = self.e.sandbox, self.profile
        if sandbox is None or p is None or self.tool_req or self.json_fmt:
            return []
        out: list[tuple[Answer, str, Any]] = []
        for answer in pending:
            program = execute.build_program(self.question, answer.text)
            if program is not None and (p.task == "code" or program.has_tests):
                out.append((answer, "code", program))
            elif p.task == "math" and self.e.settings.sandbox_math != "off":
                out.append((answer, "math", None))
        return out

    async def _execute(
        self, stage: int, runnable: list[tuple[Answer, str, Any]], heur: dict[int, Any]
    ) -> None:
        """Run each answer's code (with its tests), or compute the maths result, and turn a
        failure into a failed check whose error goes to the fix stage."""
        sandbox = self.e.sandbox
        assert sandbox is not None
        missing: set[str] = set()
        for answer, kind, program in runnable:
            if kind == "code":
                if not sandbox.available(program.language):
                    missing.add(program.language)
                    continue
                result = await asyncio.to_thread(execute.run_code, sandbox, program)
            else:
                if not sandbox.available("python"):
                    missing.add("python")
                    continue
                code, method = await self._math_program(stage)
                if code is None:
                    continue
                result = await asyncio.to_thread(
                    execute.check_math, sandbox, answer.text, code, method
                )
            self.emit("sandbox", stage=stage, model=answer.model, **result.as_dict())
            if self._logging:
                self.e.store.record_execution(self.qid, answer.stage, answer.model, result)
            if result.status == "failed" and result.error:
                if kind == "code" or result.method == "rules":
                    heur[id(answer)].fail(result.error)
                else:  # a model wrote the program: strong evidence, not proof
                    heur[id(answer)].warn(result.error, weight=4.0)
        for language in sorted(missing):
            self.emit(
                "note",
                message=f"Not run: the {language} sandbox is not installed "
                "(tempo-server sandbox install).",
            )

    async def _math_program(self, stage: int) -> tuple[str | None, str]:
        """A program that prints the answer: from rules for simple arithmetic, else written
        by a model (once per question, TEMPO_SANDBOX_MATH=auto)."""
        if self._math_code is not None:
            return self._math_code
        expression = execute.math_expression(self.question)
        if expression is not None:
            self._math_code = (execute.math_program(expression), "rules")
            return self._math_code
        self._math_code = (None, "program")
        if self.e.settings.sandbox_math != "auto" or self.requests_left() < 2:
            return self._math_code
        ranked = self._rank("check").candidates
        slots = await self._pick("check", ranked)
        if not slots:
            return self._math_code
        prompt = execute.PROGRAM_PROMPT.format(question=self.question)
        (reply,) = await self._bounded(
            [
                self._call(
                    stage, "compute", slots[0], [{"role": "user", "content": prompt}], live=False
                )
            ],
            stage=stage,
            job="compute",
            finish_started=False,
        )
        code = execute.program_from_reply(reply.text) if reply is not None else None
        self._math_code = (code, "program")
        return self._math_code

    def best(self) -> Answer | None:
        checked = [a for a in self.answers if a.check is not None]
        if not checked:
            return self.answers[-1] if self.answers else None
        return max(checked, key=lambda a: (a.score, a.stage))

    async def _improve(self) -> None:
        fixes = mixes = 0
        while True:
            if self.stages_left() <= 0:
                if self._pending():
                    self.stop_reason = "unchecked"
                    return
                raise self._stages_used()
            await self._check()
            best = self.best()
            assert best is not None and best.check is not None
            rules_level = quality_level(best.check.score)
            threshold = PASS_THRESHOLD.get(self.o.mode, PASS_THRESHOLD["auto"])
            heuristic = best.check.heuristic_score or 0.0
            baseline = {  # what the heuristics alone would decide (for `tempo-server laya compare`)
                "heuristic_level": quality_level(heuristic),
                "heuristic_passed": heuristic >= threshold and not best.check.hard_fail,
                "judged": best.check.judge_score is not None,
                "answer_model": best.model,
                "judge_model": best.check.judge_model,
            }
            level = await self.e.decide(self, "quality", self.stage, rules_level, **baseline)
            if level != rules_level:  # Laya has taken over grading
                best.check.score = level / 4
                best.check.passed = best.check.score >= threshold and not best.check.hard_fail
            stop = await self.e.decide(
                self, "should_stop", self.stage, best.check.passed, **baseline
            )
            # Never stop on an answer that failed a hard check (empty, refusal, broken JSON...).
            stop = bool(stop) and not best.check.hard_fail
            if stop:
                self.stop_reason = "passed" if best.check.passed else "decided"
                return
            left = self.stages_left()
            if left <= 0:
                raise self._stages_used()
            latest = [a for a in self.answers if a.stage == max(x.stage for x in self.answers)]
            if left == 1:
                await self._rewrite("polish")
                return
            if len(latest) >= 2:
                await self._rewrite("merge")
            elif fixes == 0:
                fixes += 1
                await self._rewrite("fix")
            elif mixes == 0 and left >= 3 and self.o.max_parallel >= 2:
                mixes += 1
                used = {self._family(a.model) for a in self.answers}
                await self._draft(min(2, self.o.max_parallel), exclude_families=used)
            elif len(self.answers) >= 2:
                await self._rewrite("merge")
            else:
                fixes += 1
                await self._rewrite("fix")

    async def _rewrite(self, job: str) -> None:
        best = self.best()
        assert best is not None
        issues = best.check.issues if best.check else []
        exclude: list[str] = []
        if job == "fix" and best.check and not best.check.passed:
            exclude = [best.model]
        route = self._rank(job, exclude=exclude)
        ranked = route.candidates or self._rank(job).candidates
        if not ranked and (self._too_slow(route) or self._too_slow(self._rank(job))):
            raise BudgetStop("time", f"no model can finish a {job} stage in the time left")
        if self.requests_left() <= 0:
            ranked = [c for c in ranked if self.e.registry.providers[c.model.provider].local]
        slots = await self._pick(job, ranked)
        if not slots:
            raise BudgetStop("quota", "free-quota budget used up")
        if job == "merge":
            top = sorted(
                [a for a in self.answers if not (a.check and a.check.hard_fail)] or self.answers,
                key=lambda a: -a.score,
            )[:3]
            merged_issues = [i for a in top if a.check for i in a.check.issues]
            messages = prompts.merge_messages(self.messages, [a.text for a in top], merged_issues)
            reason = f"combine the {len(top)} best answers"
        elif job == "polish":
            messages = prompts.polish_messages(self.messages, best.text, issues)
            reason = "last stage: final rewrite (no stage left to check it)"
        else:
            messages = prompts.fix_messages(self.messages, best.text, issues)
            reason = "fix: " + ("; ".join(issues[:3]) if issues else "raise the quality")
        stage = self._begin(job, [slots[0][0].model.id], reason)
        (result,) = await self._bounded(
            [self._call(stage, job, slots[0], messages, live=True)], stage=stage, job=job
        )
        outputs = [result] if result else []
        self._end(stage, job, outputs)
        if result is None:
            return
        if job == "polish":
            heur = run_heuristics(
                self.profile,
                self.question,
                result.text,
                result.finish_reason,
                self.json_fmt is not None,
            )
            if heur.hard_fail:
                return  # keep the best checked answer
            result.check = None
            self.final = result
            self.stop_reason = "polished"
        self.answers.append(result)
        self._maybe_ready(stage, [result])

    async def _decompose(self) -> None:
        assert self.plan is not None and self.profile is not None
        parts = self.plan.parts
        if not parts:
            parts = await self._split()
        if not parts or len(parts) < 2:
            self.plan.strategy = "cascade"
            await self._draft(1)
            return
        # Leave room for the combine stage and one check.
        part_stages = max(1, self.stages_left() - 2)
        per_stage = max(self.o.max_parallel, math.ceil(len(parts) / part_stages))
        affordable = self.requests_left() - 2
        if not self._local_available() and affordable < len(parts):
            kept = max(1, affordable)
            self.emit(
                "budget",
                reason="quota",
                detail=f"free-quota budget covers {kept} of {len(parts)} parts",
            )
            parts = parts[:kept]
        answers: list[str] = []
        for start in range(0, len(parts), per_stage):
            group = parts[start : start + per_stage]
            answers.extend(await self._parts_stage(group, start, len(parts)))
        ranked = self._rank("combine").candidates
        slots = await self._pick("combine", ranked)
        if not slots:
            raise BudgetStop("quota", "free-quota budget used up before combining")
        stage = self._begin("combine", [slots[0][0].model.id], f"combine {len(parts)} parts")
        messages = prompts.combine_parts_messages(self.messages, parts, answers)
        (result,) = await self._bounded(
            [self._call(stage, "combine", slots[0], messages, live=True)],
            stage=stage,
            job="combine",
        )
        self._end(stage, "combine", [result] if result else [])
        if result is None:
            raise _NoAnswer("No model could combine the parts.", [slots[0][0].model.id])
        self.answers.append(result)
        self._maybe_ready(stage, [result])

    async def _split(self) -> list[str] | None:
        ranked = self._rank("split").candidates
        slots = await self._pick("split", ranked)
        if not slots:
            return None
        stage = self._begin("split", [slots[0][0].model.id], "split the request into parts")
        messages = prompts.split_messages(self.messages, max(2, self.stages_left() * 3))
        (reply,) = await self._bounded(
            [self._call(stage, "split", slots[0], messages, live=False)],
            stage=stage,
            job="split",
            finish_started=False,
        )
        self._end(stage, "split", [])
        if reply is None:
            return None
        try:
            data = extract_json(reply.text)
        except ValueError:
            return None
        parts = data.get("parts") if isinstance(data, dict) else data
        if not isinstance(parts, list):
            return None
        return [str(part).strip() for part in parts if str(part).strip()][:20]

    async def _parts_stage(self, group: list[str], offset: int, total: int) -> list[str]:
        slots: list[list[Candidate]] = []
        profiles: list[QueryProfile] = []
        for part in group:
            profile = analyze([{"role": "user", "content": part}])
            profiles.append(profile)
            ranked = self._rank("draft", profile).candidates
            picked = await self._pick("parts", ranked)
            if not picked:
                raise BudgetStop("quota", "free-quota budget used up while answering parts")
            slots.append(picked[0])
        label = f"parts {offset + 1}-{offset + len(group)} of {total}"
        stage = self._begin("parts", [s[0].model.id for s in slots], label)
        calls = []
        for i, (part, slot) in enumerate(zip(group, slots, strict=True)):
            messages = prompts.part_messages(
                self.messages, part, offset + i + 1, total, self.o.system_prompt
            )
            calls.append(self._call(stage, "parts", slot, messages, live=False, slot_id=i))
        results = await self._bounded(calls, stage=stage, job="parts")
        outputs = [r for r in results if r is not None]
        self._end(stage, "parts", outputs)
        return [r.text if r else "(no answer for this part)" for r in results]

    # --- one model slot, with fallback ----------------------------------------------------

    def _claim_live(self, stage: int, slot_id: int) -> bool:
        owner = (stage, slot_id)
        if self._live_owner == owner:
            return True
        if self._live_owner is None or self._live_owner[0] < stage:
            self._live_owner = owner
            if self._shown:
                self.emit("answer_reset", stage=stage, reason=f"replaced by stage {stage}")
                self._shown = False
            return True
        return False

    async def _call(
        self,
        stage: int,
        job: str,
        slot: list[Candidate],
        messages: list[dict[str, Any]],
        *,
        live: bool,
        slot_id: int = 0,
    ) -> Answer | None:
        e, o = self.e, self.o
        live = live and o.live and self._shown_answer is None
        purpose = {"check": "judge", "tools": "draft"}.get(job, job)
        attempts = 0
        previous: str | None = None
        for candidate in slot:
            if attempts >= o.max_attempts:
                break
            model = candidate.model
            is_local = e.registry.providers[model.provider].local
            key_id = o.access.key_id(model.provider)
            if not is_local and self.requests_left() <= 0:
                continue
            if attempts and (
                e.health.unavailable_reason(model)
                or (e.quota and e.quota.blocked_reason(model, key_id))
                or estimate_seconds(model, self._job_tokens(job)) > self.time_left()
            ):
                continue
            if attempts:
                self.emit("fallback", stage=stage, **{"from": previous}, to=model.id)
            attempts += 1
            previous = model.id
            self.calls += 1
            if not is_local:
                self.requests += 1
            self.emit("call_start", stage=stage, job=job, model=model.id, attempt=attempts)

            meta: dict[str, Any] = {}
            text = reasoning = ""
            native_calls: list[dict[str, Any]] = []
            streamed = False
            started = self.clock()
            first: float | None = None
            call_messages, extra = self._prepare(job, model, messages)
            try:
                stream = e.backend_for(model).stream(
                    model,
                    call_messages,
                    temperature=o.temperature,
                    max_tokens=o.max_tokens,
                    access=o.access,
                    meta=meta,
                    purpose=purpose,
                    **extra,
                )
                async for kind, chunk in stream:
                    if first is None:
                        first = self.clock()
                    if kind == "tool_calls":
                        native_calls = json.loads(chunk)
                        continue
                    if kind == "reasoning":
                        reasoning += chunk
                        if live and self._claim_live(stage, slot_id):
                            self.emit("reasoning_delta", stage=stage, model=model.id, delta=chunk)
                        continue
                    text += chunk
                    self._started.add((stage, slot_id))
                    self._partial[(stage, slot_id)] = (model.id, text)
                    if live and self._claim_live(stage, slot_id):
                        streamed = True
                        self._shown = True
                        self.emit("answer_delta", stage=stage, model=model.id, delta=chunk)
                if (
                    meta.get("finish_reason") == "length"
                    and text.strip()
                    and job in ANSWER_JOBS | {"parts"}
                    and not native_calls
                ):
                    text, meta["finish_reason"] = await self._continue(
                        stage, job, slot, model, call_messages, text, live, slot_id
                    )
                if not text.strip() and not native_calls:
                    raise ProviderError("empty", "The model returned no answer text.")
                outcome = self._validate(job, text, native_calls)
                if outcome.issues:
                    # Unusable for this request (a bad tool call, JSON that doesn't match the
                    # schema): not a provider failure, so no cool-down; the next model is tried.
                    raise ProviderError("invalid", "; ".join(outcome.issues[:3]))
                text = outcome.text
            except ProviderError as err:
                if err.kind == "invalid":
                    self._last_invalid = err.message
                if (
                    "response_format" in extra
                    and err.kind == "bad_request"
                    and _REJECTED_FORMAT.search(err.message)
                ):
                    # The provider rejected native structured output: never send it to this
                    # model again (saved with the catalog); the prompt-and-validate path stays.
                    model.structured_outputs = False
                    log.info("%s rejected response_format; not sending it again", model.id)
                e.health.record_failure(model, err.kind, err.retry_after)
                self._usage(model, key_id, messages, "", meta)
                self._log_call(stage, job, model, "error", err.kind, started, first, messages, "")
                self.emit(
                    "call_error", stage=stage, model=model.id, kind=err.kind, message=err.message
                )
                if streamed:
                    if not o.restart_on_partial_failure:
                        self.emit(
                            "error",
                            message=f"{model.id} failed mid-answer: {ERROR_LABELS[err.kind]}",
                            kind=err.kind,
                        )
                        raise StreamBroken() from err
                    self.emit("answer_reset", stage=stage, reason="model failed mid-answer")
                    self._shown = False
                continue

            e.health.record_success(model)
            self._usage(model, key_id, messages, text, meta)
            call_id = self._log_call(stage, job, model, "ok", None, started, first, messages, text)
            now = self.clock()
            e.speed.record(
                model,
                (first - started) if first is not None else None,
                now - started,
                (len(text) + len(reasoning)) // 4,
            )
            self.emit(
                "call_end",
                stage=stage,
                job=job,
                model=model.id,
                ms=round((now - started) * 1000),
                ttft_ms=round((first - started) * 1000) if first is not None else None,
            )
            return Answer(
                text=text,
                model=model.id,
                stage=stage,
                job=job,
                reasoning=reasoning,
                finish_reason=meta.get("finish_reason"),
                call_id=call_id,
                tool_calls=outcome.tool_calls,
            )
        return None

    async def _continue(
        self,
        stage: int,
        job: str,
        slot: list[Candidate],
        model: ModelInfo,
        messages: list[dict[str, Any]],
        text: str,
        live: bool,
        slot_id: int,
    ) -> tuple[str, str | None]:
        """The model stopped at its output limit (finish_reason "length"): ask it, or the next
        model in the slot, for the rest, and join the pieces. Up to MAX_CONTINUATIONS rounds;
        the answer is arriving, so the time budget doesn't stop it (the grace period does)."""
        e, o = self.e, self.o
        finish: str | None = "length"
        order = [model] + [c.model for c in slot if c.model.id != model.id]
        rounds = 0
        while finish == "length" and rounds < MAX_CONTINUATIONS:
            rounds += 1
            previous = model.id
            for candidate in order:
                is_local = e.registry.providers[candidate.provider].local
                key_id = o.access.key_id(candidate.provider)
                if not is_local and self.requests_left() <= 0:
                    continue
                if e.health.unavailable_reason(candidate) or (
                    e.quota and e.quota.blocked_reason(candidate, key_id)
                ):
                    continue
                self.emit("continue", stage=stage, model=previous, to=candidate.id, round=rounds)
                self.calls += 1
                if not is_local:
                    self.requests += 1
                meta: dict[str, Any] = {}
                started = self.clock()
                first: float | None = None
                more = ""
                pending = ""  # the start of the new piece, until any repeated text is removed
                cont = prompts.continue_messages(messages, text)
                try:
                    stream = e.backend_for(candidate).stream(
                        candidate,
                        cont,
                        temperature=o.temperature,
                        max_tokens=o.max_tokens,
                        access=o.access,
                        meta=meta,
                        purpose="continue",
                    )
                    async for kind, chunk in stream:
                        if kind != "answer":
                            continue
                        if first is None:
                            first = self.clock()
                        if pending is not None:
                            pending += chunk
                            if len(pending) < OVERLAP_WINDOW:
                                continue
                            chunk, pending = _strip_overlap(text + more, pending), None
                        more += chunk
                        self._partial[(stage, slot_id)] = (model.id, text + more)
                        if chunk and live and self._claim_live(stage, slot_id):
                            self.emit("answer_delta", stage=stage, model=candidate.id, delta=chunk)
                    if pending:
                        chunk = _strip_overlap(text + more, pending)
                        more += chunk
                        if chunk and live and self._claim_live(stage, slot_id):
                            self.emit("answer_delta", stage=stage, model=candidate.id, delta=chunk)
                    if not more.strip():
                        raise ProviderError("empty", "The model returned nothing to add.")
                except ProviderError as err:
                    e.health.record_failure(candidate, err.kind, err.retry_after)
                    self._usage(candidate, key_id, cont, "", meta)
                    self._log_call(
                        stage, "continue", candidate, "error", err.kind, started, first, cont, ""
                    )
                    self.emit(
                        "call_error",
                        stage=stage,
                        model=candidate.id,
                        kind=err.kind,
                        message=err.message,
                    )
                    continue
                e.health.record_success(candidate)
                e.speed.record(
                    candidate,
                    (first - started) if first is not None else None,
                    self.clock() - started,
                    len(more) // 4,
                )
                self._usage(candidate, key_id, cont, more, meta)
                self._log_call(stage, "continue", candidate, "ok", None, started, first, cont, more)
                text += more
                finish = meta.get("finish_reason")
                model = candidate
                break
            else:
                self.emit(
                    "note",
                    message="The answer stopped at the model's output limit and no model could "
                    "continue it: it is shown as it is.",
                )
                break
        return text, finish

    def _usage(
        self,
        model: ModelInfo,
        key_id: str,
        messages: list[dict[str, Any]],
        text: str,
        meta: dict[str, Any],
    ) -> None:
        if self.e.quota is None:
            return
        tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4 + len(text) // 4
        self.e.quota.record(model, key_id, tokens)
        self.e.quota.observe_headers(model, key_id, meta.get("headers"))

    def _log_call(
        self,
        stage: int,
        job: str,
        model: ModelInfo,
        status: str,
        error_kind: str | None,
        started: float,
        first: float | None,
        messages: list[dict[str, Any]],
        text: str,
    ) -> int | None:
        if not self.e.store or not self.o.log:
            return None
        now = self.clock()
        return self.e.store.add_call(
            question_id=self.qid,
            stage=stage,
            job=job,
            model=model.id,
            provider=model.provider,
            task=self.profile.task if self.profile and job in ("draft", "fix", "merge") else None,
            status=status,
            error_kind=error_kind,
            ms=round((now - started) * 1000),
            ttft_ms=round((first - started) * 1000) if first is not None else None,
            input_tokens=sum(len(str(m.get("content", ""))) for m in messages) // 4,
            output_tokens=len(text) // 4,
            licence=model.licence,
            training_verdict=self.e.registry.training_verdict(model),
        )

    def _family(self, model_id: str) -> str:
        model = self.e.registry.get(model_id)
        return model.family if model else model_id

    def _failure_message(self) -> str:
        if self._time_up:
            return (
                f"No model answered within the {self.o.time_budget_s:g}s time budget. Try Fast "
                "mode, a longer time budget, or a local model."
            )
        if self.requests_left() <= 0:
            return "The free-quota budget for this question ran out before any model answered."
        if self._last_invalid:
            what = "tool call" if self.tool_req else "JSON answer"
            return f"No model gave a valid {what}. Last problem: {self._last_invalid}"
        return "Every model tried for this stage failed."

    # --- finish ----------------------------------------------------------------------------

    def _maybe_ready(self, stage: int, results: Sequence[Answer | None]) -> None:
        """The answer that streamed in this stage is shown as ready as soon as it passes the
        quick checks; anything later happens in the background."""
        if self._shown_answer is not None or not self.o.live or self._live_owner is None:
            return
        owner_stage, slot = self._live_owner
        if owner_stage != stage or slot >= len(results) or results[slot] is None:
            return
        answer = results[slot]
        heur = run_heuristics(
            self.profile,
            self.question,
            answer.text,
            answer.finish_reason,
            self.json_fmt is not None,
        )
        if heur.hard_fail:
            return  # not good enough to show as ready: later stages stream as before
        self._shown_answer = answer
        checking = self.stages_left() > 0 and not self._time_up and self.final is None
        self.emit(
            "answer_ready",
            answer=answer.text,
            model=answer.model,
            stage=stage,
            checking=checking,
        )

    def _revise(self, answer: Answer) -> None:
        """A better answer replaces the one shown: say what the check found and which model
        wrote the new one (the client shows the difference)."""
        shown = self._shown_answer
        if shown is None or answer.text.strip() == shown.text.strip():
            self._shown_answer = answer
            return
        issues = (shown.check.issues if shown.check else []) or []
        self.emit(
            "answer_revised",
            answer=answer.text,
            model=answer.model,
            stage=answer.stage,
            job=answer.job,
            previous_model=shown.model,
            previous_stage=shown.stage,
            issues=issues[:5],
            score=answer.check.score if answer.check else None,
            previous_score=shown.check.score if shown.check else None,
        )
        self._shown_answer = answer

    def _late_improvement(self) -> Answer | None:
        """A fix, merge or combine that finished after the time budget ended: nothing could
        check it, but it was written to improve on the best answer, so it wins unless the quick
        checks fail it (empty, cut off, refusal, wrong language...)."""
        if not self._time_up:
            return None
        for answer in reversed(self._pending()):
            if answer.job not in ("fix", "merge", "combine", "polish"):
                continue
            heur = run_heuristics(
                self.profile,
                self.question,
                answer.text,
                answer.finish_reason,
                self.json_fmt is not None,
            )
            if not heur.hard_fail:
                return answer
        return None

    def _finish(self) -> None:
        final = self.final or self._late_improvement() or self.best()
        if final is None:
            self.emit(
                "error",
                message=self._failure_message(),
                kind="budget" if self._time_up else "unavailable",
            )
            self._log(error="no answer within budget")
            return
        self.final = final
        if self._shown_answer is not None and final is not self._shown_answer:
            self._revise(final)
        if self.stop_reason == "passed" and not (final.check and final.check.passed):
            self.stop_reason = "not_passed"  # e.g. the last rewrite failed a hard check too
        self.emit(
            "answer_final",
            answer=final.text,
            model=final.model,
            stage=final.stage,
            score=final.check.score if final.check else None,
            passed=bool(final.check and final.check.passed),
            reasoning=final.reasoning or None,
            tool_calls=final.tool_calls or None,
            note=self._budget_note(final),
        )
        if (
            self.e.cache
            and self.o.use_cache
            and self._cacheable()
            and final.check is not None
            and final.check.passed
        ):
            self.e.cache.store(
                self.question, self.o.mode, final.text, final.model, self.o.access.user_id
            )
        self._done()

    def _budget_note(self, final: Answer) -> str | None:
        """A plain note when a budget ended the work with an answer in hand."""
        if not self.stop_reason.startswith("budget_"):
            return None
        what = {
            "budget_time": f"the {self.o.time_budget_s:g}s time budget",
            "budget_stages": f"the {self.max_stages}-stage budget",
            "budget_quota": "the free-request budget",
        }.get(self.stop_reason, "the budget")
        if final.check is not None:
            quality = f"checked, score {final.check.score:.2f}"
        else:
            quality = "not checked yet"
        return (
            f"Stopped by {what} after {self.stage} stage{'' if self.stage == 1 else 's'}: "
            "this is the best answer so far "
            f"({quality})."
        )

    def _done(self) -> None:
        total_ms = round((self.clock() - self.t0) * 1000)
        final = self.final
        self.emit(
            "done",
            question_id=self.qid,
            model=final.model if final else None,
            stages=self.stage,
            attempts=self.calls,
            requests=self.requests,
            total_ms=total_ms,
            stop_reason=self.stop_reason,
            strategy=self.plan.strategy if self.plan else None,
        )
        self._log(
            final_answer=final.text if final else None,
            final_model=final.model if final else None,
            final_score=final.check.score if final and final.check else None,
            stop_reason=self.stop_reason,
            stages_used=self.stage,
            requests_used=self.requests,
            total_ms=total_ms,
            cache_hit=1 if self.stop_reason == "cache" else 0,
        )

    # --- logging ---------------------------------------------------------------------------

    @property
    def _logging(self) -> bool:
        return bool(self.e.store and self.e.settings.log_questions and self.o.log)

    def _log_start(self) -> None:
        if self._logging:
            self.e.store.start_question(
                self.qid,
                user_id=self.o.access.user_id,
                mode=self.o.mode,
                messages=self.messages,
                source=self.o.source,
            )

    def _log(self, **fields: Any) -> None:
        if not self._logging:
            return
        if self.profile is not None:
            fields.setdefault("profile", self.profile.model_dump())
        if self.plan is not None:
            fields.setdefault(
                "plan",
                {
                    "strategy": self.plan.strategy,
                    "stage_budget": self.plan.stage_budget,
                    "drafts": self.plan.drafts,
                    "parts": self.plan.parts,
                },
            )
        self.e.store.update_question(self.qid, **fields)


class _NoAnswer(Exception):
    def __init__(self, message: str, tried: list[str], kind: str = "unavailable") -> None:
        super().__init__(message)
        self.tried = tried
        self.kind = kind


ANSWER_JOBS = frozenset({"draft", "fix", "merge", "polish", "combine"})
MAX_CONTINUATIONS = 3  # rounds of "continue where you stopped" after an output-limit stop
OVERLAP_WINDOW = 200  # characters of a continuation checked for text it repeated


def _strip_overlap(before: str, piece: str) -> str:
    """A continuation often starts by repeating the last words it saw: drop that part."""
    for size in range(min(len(before), len(piece), OVERLAP_WINDOW), 7, -1):
        if before.endswith(piece[:size]):
            return piece[size:]
    return piece


_REJECTED_FORMAT = re.compile(
    r"response_format|json_schema|structured output|schema", re.IGNORECASE
)


def _with_system(messages: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    return [{"role": "system", "content": text}, *messages]


def _merge_systems(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One system message first (some providers reject several)."""
    systems = [message_text(m.get("content")) for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if not systems:
        return rest
    return [{"role": "system", "content": "\n\n".join(s for s in systems if s)}, *rest]
