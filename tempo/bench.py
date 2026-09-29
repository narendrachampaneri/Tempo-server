"""`tempo-server bench`: how fast answers come, per mode, with your own keys.

For each mode it asks a few standard questions and times three moments: the first token of
the answer, the answer shown as ready (it can be read and copied; checking may go on), and the
end (every stage done). Runs are not logged as questions and skip the answer cache, so they
measure the models, not the cache; their timings do update the speed record the router uses.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tempo.engine import DEFAULT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from tempo.engine import Engine

QUESTIONS = [
    "What is the capital of Australia? Answer in one line.",
    "Write a Python function that checks whether a number is prime, with a short docstring.",
    "Explain in two short paragraphs why the sky is blue.",
]


@dataclass
class Run:
    mode: str
    question: str
    first_token_s: float | None = None
    ready_s: float | None = None
    total_s: float | None = None
    model: str | None = None
    stages: int = 0
    requests: int = 0
    error: str | None = None


@dataclass
class ModeResult:
    mode: str
    runs: list[Run] = field(default_factory=list)

    @staticmethod
    def _median(values: list[float | None]) -> float | None:
        numbers = [v for v in values if v is not None]
        return round(statistics.median(numbers), 2) if numbers else None

    def summary(self) -> dict[str, Any]:
        ok = [r for r in self.runs if r.error is None]
        return {
            "mode": self.mode,
            "questions": len(self.runs),
            "errors": len(self.runs) - len(ok),
            "first_token_s": self._median([r.first_token_s for r in ok]),
            "ready_s": self._median([r.ready_s for r in ok]),
            "total_s": self._median([r.total_s for r in ok]),
            "slowest_total_s": max((r.total_s or 0 for r in ok), default=None),
            "requests": sum(r.requests for r in self.runs),
            "models": sorted({r.model for r in ok if r.model}),
        }


async def one(engine: Engine, mode: str, question: str, **stage_options: Any) -> Run:
    options = engine.options(mode=mode, system_prompt=DEFAULT_SYSTEM_PROMPT, **stage_options)
    options.log = False
    options.use_cache = False
    run = Run(mode, question)
    started = time.perf_counter()
    async for event in engine.run([{"role": "user", "content": question}], options):
        elapsed = round(time.perf_counter() - started, 3)
        if event.type == "answer_delta" and run.first_token_s is None:
            run.first_token_s = elapsed
        elif event.type == "answer_ready" and run.ready_s is None:
            run.ready_s = elapsed
        elif event.type == "answer_final":
            run.model = event.data.get("model")
            if run.ready_s is None:
                run.ready_s = elapsed
            if run.first_token_s is None:  # a client that got the whole answer at once
                run.first_token_s = elapsed
        elif event.type == "done":
            run.total_s = elapsed
            run.stages = event.data.get("stages", 0)
            run.requests = event.data.get("requests", 0)
        elif event.type == "error":
            run.error = event.data.get("message", "error")
    return run


async def run_bench(
    engine: Engine,
    modes: list[str],
    questions: list[str] | None = None,
    repeat: int = 1,
    on_run: Any = None,
    **stage_options: Any,
) -> list[ModeResult]:
    """Ask every question in every mode (one at a time, so runs don't slow each other)."""
    await engine.startup(oneshot=True)
    results = []
    for mode in modes:
        result = ModeResult(mode)
        for _ in range(repeat):
            for question in questions or QUESTIONS:
                run = await one(engine, mode, question, **stage_options)
                result.runs.append(run)
                if on_run is not None:
                    on_run(run)
        results.append(result)
    if engine.laya is not None:
        await engine.laya.drain()
    return results
