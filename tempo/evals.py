"""Measured skill scores.

``tempo eval`` sends a small probe set (tempo/data/evalset.yaml) to each model, grades every
reply automatically, and stores a score per (model, task). Real traffic adds the judge's scores
from check stages. ``SkillBook`` blends both with the registry's priors, weighted by how many
samples back them, and the router uses the result as each model's skill.
"""

from __future__ import annotations

import ast
import asyncio
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

import yaml

from tempo.checks import extract_json
from tempo.providers import ProviderError
from tempo.registry import Registry
from tempo.store import Store
from tempo.types import TASKS, Access, ModelInfo

if TYPE_CHECKING:
    from tempo.engine import Engine

PRIOR_WEIGHT = 5.0  # a prior counts like five measured samples
LIVE_WEIGHT = 0.5  # a judged real answer counts half as much as a graded probe
REFRESH_S = 300.0

_FENCE = re.compile(r"```(?:python|py|python3)?\n(.*?)```", re.DOTALL)
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class EvalItem:
    task: str
    question: str
    grade: dict[str, Any]


def load_evalset() -> list[EvalItem]:
    text = resources.files("tempo").joinpath("data/evalset.yaml").read_text("utf-8")
    data = yaml.safe_load(text) or {}
    return [
        EvalItem(task, str(item["q"]), dict(item["grade"]))
        for task in TASKS
        for item in data.get(task) or []
    ]


def grade(answer: str, rule: dict[str, Any]) -> float:
    """1.0 if the answer satisfies the rule, else 0.0. Code is parsed, never executed."""
    text = answer.strip()
    low = text.lower()
    if "contains" in rule:
        return float(all(str(p).lower() in low for p in rule["contains"]))
    if "any" in rule:
        return float(any(str(p).lower() in low for p in rule["any"]))
    if "number" in rule:
        target = float(rule["number"])
        tolerance = float(rule.get("tolerance", max(0.01, abs(target) * 0.001)))
        for match in _NUMBER.findall(text):
            try:
                if abs(float(match.replace(",", "")) - target) <= tolerance:
                    return 1.0
            except ValueError:
                continue
        return 0.0
    if "json" in rule:
        try:
            data = extract_json(text)
        except ValueError:
            return 0.0
        if not isinstance(data, dict):
            return 0.0
        wanted = rule["json"]
        return float(
            all(
                str(data.get(k)).strip().lower() == str(v).strip().lower()
                for k, v in wanted.items()
            )
        )
    if "python" in rule:
        blocks = _FENCE.findall(text) or [text]
        for code in blocks:
            try:
                tree = ast.parse(code)
            except SyntaxError:
                continue
            names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            if rule["python"] in names:
                return 1.0
        return 0.0
    raise ValueError(f"Unknown grading rule: {rule}")


@dataclass
class EvalResult:
    model: str
    task: str
    n: int
    score: float
    errors: int


async def _ask(engine: Engine, model: ModelInfo, question: str) -> str | None:
    access = Access()
    key_id = access.key_id(model.provider)
    if engine.quota.blocked_reason(model, key_id):
        return None
    messages = [
        {"role": "system", "content": "Answer exactly as asked, as briefly as possible."},
        {"role": "user", "content": question},
    ]
    meta: dict[str, Any] = {}
    text = ""
    try:
        async for kind, chunk in engine.backend_for(model).stream(
            model, messages, access=access, meta=meta, purpose="eval", temperature=0.0
        ):
            if kind == "answer":
                text += chunk
    except ProviderError as err:
        engine.health.record_failure(model, err.kind, err.retry_after)
        return None
    finally:
        engine.quota.record(model, key_id, len(question) // 4 + len(text) // 4)
        engine.quota.observe_headers(model, key_id, meta.get("headers"))
    return text


async def run_evals(
    engine: Engine,
    models: Iterable[ModelInfo],
    tasks: Iterable[str] | None = None,
    limit: int | None = None,
    on_result: Callable[[EvalResult], None] | None = None,
) -> list[EvalResult]:
    """Grade each model on the probe set and save the scores. Models run in parallel."""
    wanted = set(tasks or TASKS)
    items = [item for item in load_evalset() if item.task in wanted]
    by_task: dict[str, list[EvalItem]] = {}
    for item in items:
        by_task.setdefault(item.task, []).append(item)
    if limit:
        by_task = {task: group[:limit] for task, group in by_task.items()}

    async def one_model(model: ModelInfo) -> list[EvalResult]:
        results = []
        for task, group in by_task.items():
            scores: list[float] = []
            errors = 0
            for item in group:
                answer = await _ask(engine, model, item.question)
                if answer is None:
                    errors += 1
                    continue
                scores.append(grade(answer, item.grade))
            if scores:
                mean = sum(scores) / len(scores)
                engine.store.save_eval(model.id, task, len(scores), mean)
            result = EvalResult(
                model.id, task, len(scores), sum(scores) / max(1, len(scores)), errors
            )
            if on_result:
                on_result(result)
            results.append(result)
        return results

    nested = await asyncio.gather(*(one_model(m) for m in models))
    engine.skills.refresh(force=True)
    return [r for group in nested for r in group]


class SkillBook:
    """Skill per (model, task): registry prior blended with probe and live measurements."""

    def __init__(
        self,
        registry: Registry,
        store: Store | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.store = store
        self._clock = clock
        self._loaded = -REFRESH_S
        self._evals: dict[tuple[str, str], tuple[int, float]] = {}
        self._live: dict[tuple[str, str], tuple[int, float]] = {}
        self._prior_latency: dict[str, tuple[int, float]] = {}

    def refresh(self, force: bool = False) -> None:
        if self.store is None or (not force and self._clock() - self._loaded < REFRESH_S):
            return
        self._loaded = self._clock()
        self._evals = {
            (r["model"], r["task"]): (r["n"], r["score"]) for r in self.store.eval_results()
        }
        self._live = {
            (r["model"], r["task"]): (r["n"], r["score"]) for r in self.store.live_scores()
        }
        for row in self.store.live_latency():
            model = self.registry.get(row["model"])
            if model is None or row["n"] < 5:
                continue
            prior = self._prior_latency.setdefault(model.id, (model.ttft_ms, model.tokens_per_sec))
            weight = row["n"] / (row["n"] + PRIOR_WEIGHT)
            if row["ttft_ms"] is not None:
                model.ttft_ms = round(prior[0] * (1 - weight) + row["ttft_ms"] * weight)
            if row["out_tokens"] and row["gen_ms"] and row["gen_ms"] > 0:
                measured_tps = row["out_tokens"] / (row["gen_ms"] / 1000)
                model.tokens_per_sec = prior[1] * (1 - weight) + measured_tps * weight

    def detail(self, model: ModelInfo, task: str) -> dict[str, Any]:
        self.refresh()
        prior = model.skill(task)
        n_eval, s_eval = self._evals.get((model.id, task), (0, 0.0))
        n_live, s_live = self._live.get((model.id, task), (0, 0.0))
        weight = PRIOR_WEIGHT + n_eval + LIVE_WEIGHT * n_live
        blended = (PRIOR_WEIGHT * prior + n_eval * s_eval + LIVE_WEIGHT * n_live * s_live) / weight
        return {
            "prior": prior,
            "eval_n": n_eval,
            "eval_score": s_eval if n_eval else None,
            "live_n": n_live,
            "live_score": s_live if n_live else None,
            "skill": round(blended, 4),
        }

    def skill(self, model: ModelInfo, task: str) -> float:
        return self.detail(model, task)["skill"]
