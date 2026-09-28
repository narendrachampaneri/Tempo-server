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
import logging
import math
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tempo import prompts
from tempo.analyzer import analyze
from tempo.checks import (
    PASS_THRESHOLD,
    CheckResult,
    combine,
    extract_json,
    parse_judge,
    run_heuristics,
)
from tempo.events import Event
from tempo.laya_decider import DIFFICULTY_COMPLEXITY, complexity_level, quality_level
from tempo.providers import ERROR_LABELS, ProviderError
from tempo.router import Candidate
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

    async def _timed(self, awaitable: Any) -> Any:
        left = self.time_left()
        if left <= 0:
            raise BudgetStop("time", "time budget used up")
        try:
            return await asyncio.wait_for(awaitable, timeout=left)
        except TimeoutError as exc:
            raise BudgetStop("time", f"time budget of {self.o.time_budget_s:g}s reached") from exc

    def _need_stage(self, job: str) -> None:
        if self.stages_left() <= 0:
            raise BudgetStop("stages", f"stage budget of {self.max_stages} used")
        if self.time_left() <= 0:
            raise BudgetStop("time", "time budget used up")

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
            )
            if reason:
                message = f"{o.model} is unavailable: {reason}"
                self.emit("error", message=message, kind="unavailable")
                self._log(error="requested model unavailable")
                return

        route = self._rank("draft")
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
        self.emit(
            "plan",
            strategy=self.plan.strategy,
            max_stages=self.max_stages,
            drafts=self.plan.drafts,
            parts=len(self.plan.parts or []),
            time_budget_s=o.time_budget_s,
            quota_budget=o.quota_budget,
            reason=self.plan.reasons.get("strategy", ""),
            laya=e.laya.status_text() if e.laya is not None else None,
        )

        try:
            if self.plan.strategy == "decompose":
                await self._decompose()
            else:
                await self._draft(self.plan.drafts)
            await self._improve()
        except BudgetStop as stop:
            self.stop_reason = f"budget_{stop.reason}"
            self.emit("budget", reason=stop.reason, detail=stop.detail)
        except _NoAnswer as failure:
            self.emit("error", message=str(failure), kind=failure.kind, tried=failure.tried)
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
        turns = [m for m in self.messages if m.get("role") in ("user", "assistant")]
        return len(turns) == 1 and self.o.model is None and not self.o.allow_providers

    # --- planning ------------------------------------------------------------------

    async def _make_plan(self) -> Plan:
        o, p = self.o, self.profile
        assert p is not None
        parts = prompts.rule_split(self.question)
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

    # --- ranking and model choice ----------------------------------------------------

    def _rank(
        self,
        job: str,
        profile: QueryProfile | None = None,
        exclude: Sequence[str] = (),
    ):
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
        )

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
        if self.e.store and self.e.settings.log_questions:
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
        ranked = self._rank("draft").candidates
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
        count = self._affordable(count, ranked)
        slots = await self._pick("draft", ranked, count)
        if not slots:
            raise BudgetStop("quota", "no model left within the free-quota budget")
        job = "draft"
        reason = slots[0][0].why if count == 1 else f"{count} models from different families"
        stage = self._begin(job, [s[0].model.id for s in slots], reason)
        messages = prompts.draft_messages(self.messages, self.o.system_prompt)
        results = await self._timed(
            asyncio.gather(
                *(
                    self._call(stage, job, slot, messages, live=True, slot_id=i)
                    for i, slot in enumerate(slots)
                )
            )
        )
        outputs = [r for r in results if r is not None]
        self._end(stage, job, outputs)
        if not outputs:
            raise _NoAnswer(self._failure_message(), [c.model.id for s in slots for c in s])
        self.answers.extend(outputs)

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
        heur = {id(a): run_heuristics(p, self.question, a.text, a.finish_reason) for a in pending}
        gradable = [a for a in pending if not heur[id(a)].hard_fail]
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
        stage = self._begin("check", [judge_slot[0].model.id] if judge_slot else [], reason)
        grades = None
        judge_model = None
        if judge_slot:
            messages = prompts.judge_messages(self.messages, [a.text for a in gradable])
            reply = await self._timed(self._call(stage, "check", judge_slot, messages, live=False))
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
                raise BudgetStop("stages", f"stage budget of {self.max_stages} used")
            await self._check()
            best = self.best()
            assert best is not None and best.check is not None
            rules_level = quality_level(best.check.score)
            threshold = PASS_THRESHOLD.get(self.o.mode, PASS_THRESHOLD["auto"])
            heuristic = best.check.heuristic_score or 0.0
            baseline = {  # what the heuristics alone would decide (for `tempo laya compare`)
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
                raise BudgetStop("stages", f"stage budget of {self.max_stages} used")
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
        ranked = self._rank(job, exclude=exclude).candidates or self._rank(job).candidates
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
        result = await self._timed(self._call(stage, job, slots[0], messages, live=True))
        outputs = [result] if result else []
        self._end(stage, job, outputs)
        if result is None:
            return
        if job == "polish":
            heur = run_heuristics(self.profile, self.question, result.text, result.finish_reason)
            if heur.hard_fail:
                return  # keep the best checked answer
            result.check = None
            self.final = result
            self.stop_reason = "polished"
        self.answers.append(result)

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
        result = await self._timed(self._call(stage, "combine", slots[0], messages, live=True))
        self._end(stage, "combine", [result] if result else [])
        if result is None:
            raise _NoAnswer("No model could combine the parts.", [slots[0][0].model.id])
        self.answers.append(result)

    async def _split(self) -> list[str] | None:
        ranked = self._rank("split").candidates
        slots = await self._pick("split", ranked)
        if not slots:
            return None
        stage = self._begin("split", [slots[0][0].model.id], "split the request into parts")
        messages = prompts.split_messages(self.messages, max(2, self.stages_left() * 3))
        reply = await self._timed(self._call(stage, "split", slots[0], messages, live=False))
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
        results = await self._timed(asyncio.gather(*calls))
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
        live = live and o.live
        purpose = "judge" if job == "check" else job
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
            streamed = False
            started = self.clock()
            first: float | None = None
            try:
                stream = e.backend_for(model).stream(
                    model,
                    messages,
                    temperature=o.temperature,
                    max_tokens=o.max_tokens,
                    access=o.access,
                    meta=meta,
                    purpose=purpose,
                )
                async for kind, chunk in stream:
                    if first is None:
                        first = self.clock()
                    if kind == "reasoning":
                        reasoning += chunk
                        if live and self._claim_live(stage, slot_id):
                            self.emit("reasoning_delta", stage=stage, model=model.id, delta=chunk)
                        continue
                    text += chunk
                    if live and self._claim_live(stage, slot_id):
                        streamed = True
                        self._shown = True
                        self.emit("answer_delta", stage=stage, model=model.id, delta=chunk)
                if not text.strip():
                    raise ProviderError("empty", "The model returned no answer text.")
            except ProviderError as err:
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
            )
        return None

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
        if not self.e.store:
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
        if self.requests_left() <= 0:
            return "The free-quota budget for this question ran out before any model answered."
        return "Every model tried for this stage failed."

    # --- finish ----------------------------------------------------------------------------

    def _finish(self) -> None:
        final = self.final or self.best()
        if final is None:
            self.emit(
                "error",
                message="No answer was produced within the budget.",
                kind="budget",
            )
            self._log(error="no answer within budget")
            return
        self.final = final
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

    def _log_start(self) -> None:
        if self.e.store and self.e.settings.log_questions:
            self.e.store.start_question(
                self.qid,
                user_id=self.o.access.user_id,
                mode=self.o.mode,
                messages=self.messages,
            )

    def _log(self, **fields: Any) -> None:
        if not (self.e.store and self.e.settings.log_questions):
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
