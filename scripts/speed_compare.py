"""Speed with fake providers: the step 10 laptop test, replayed without network or keys.

    python scripts/speed_compare.py                     # this checkout
    python scripts/speed_compare.py --budget 30 --modes best
    git worktree add ../tempo-old ec764cc               # main before step 10
    PYTHONPATH=../tempo-old python scripts/speed_compare.py

Fake models, with the timings seen on the laptop (real seconds):

    groq llama-3.3-70b          first token 0.3 s, done 1.5 s
    groq gpt-oss-120b           0.5 s / 2.5 s (a reasoning model)
    nvidia nemotron-3-super     6 s / 43 s: the strongest and fast on paper, slow in practice
    gemini 2.5 flash and pro    in models.yaml, gone at the provider: "model not found"
    gemini-3-flash              1.5 s / 6 s

Each mode answers three questions three times with one engine, so measured speed can be
learnt. The run uses a virtual clock (an event loop that jumps to the next timer), so a 43 s
call takes no wall time; everything is in memory. "On screen" is when a complete answer is
shown: the answer_ready event, or the final answer on code without it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from typing import Any

os.environ.setdefault("TEMPO_EMBEDDINGS", "off")
os.environ.setdefault("TEMPO_LAYA", "off")

import httpx  # noqa: E402

import tempo  # noqa: E402
from tempo.config import Settings  # noqa: E402
from tempo.engine import Engine  # noqa: E402
from tempo.health import HealthTracker  # noqa: E402
from tempo.providers import ProviderError  # noqa: E402
from tempo.registry import Registry  # noqa: E402
from tempo.types import TASKS, ModelInfo, ProviderInfo  # noqa: E402

NOT_FOUND = "not_found"
TIMING: dict[str, tuple[float, float] | str] = {  # (first token, done) in seconds
    "groq/llama-3.3-70b-versatile": (0.3, 1.5),
    "groq/openai/gpt-oss-120b": (0.5, 2.5),
    "nvidia/nvidia/nemotron-3-super-120b-a12b": (6.0, 43.0),
    "gemini/gemini-2.5-flash": NOT_FOUND,
    "gemini/gemini-2.5-pro": NOT_FOUND,
    "gemini/gemini-3-flash": (1.5, 6.0),
}
# What the registry believes before any call is measured.
PAPER: dict[str, dict[str, Any]] = {
    "groq/llama-3.3-70b-versatile": {"strength": 0.72, "ttft_ms": 300, "tokens_per_sec": 280},
    "groq/openai/gpt-oss-120b": {
        "strength": 0.8,
        "ttft_ms": 500,
        "tokens_per_sec": 250,
        "reasoning": True,
    },
    "nvidia/nvidia/nemotron-3-super-120b-a12b": {
        "strength": 0.9,
        "ttft_ms": 800,
        "tokens_per_sec": 150,
        "reasoning": True,
    },
    "gemini/gemini-2.5-flash": {"strength": 0.85, "ttft_ms": 600, "tokens_per_sec": 200},
    "gemini/gemini-2.5-pro": {
        "strength": 0.92,
        "ttft_ms": 1500,
        "tokens_per_sec": 80,
        "reasoning": True,
    },
    "gemini/gemini-3-flash": {"strength": 0.82, "ttft_ms": 700, "tokens_per_sec": 200},
}
FAMILY = {"groq/llama": "llama", "groq/openai": "gpt-oss"}
KEYS = {  # placeholders: nothing leaves this process
    "GROQ_API_KEY": "gsk_placeholder",
    "NVIDIA_API_KEY": "nvapi-placeholder",
    "GEMINI_API_KEY": "placeholder",
}
QUESTIONS = {
    "easy": "What is the capital of France?",
    "code": "Write a Python function that checks whether a string is a palindrome.",
    "explain": "Explain how HTTPS keeps a connection private, step by step.",
}
ROUNDS = 3
CODE = "\n\n```python\ndef is_pal(s):\n    s = s.lower()\n    return s == s[::-1]\n```\n"


class VirtualLoop(asyncio.SelectorEventLoop):
    """The clock jumps to the next timer whenever nothing else is ready to run."""

    def __init__(self) -> None:
        super().__init__()
        self._now = 0.0

    def time(self) -> float:
        return self._now

    def _run_once(self) -> None:
        if not self._ready and self._scheduled:  # type: ignore[attr-defined]
            self._now = max(self._now, self._scheduled[0]._when)  # type: ignore[attr-defined]
        super()._run_once()  # type: ignore[misc]


def registry() -> Registry:
    providers = {
        "groq": ProviderInfo(id="groq", label="Groq", key_env="GROQ_API_KEY"),
        "nvidia": ProviderInfo(id="nvidia", label="NVIDIA", key_env="NVIDIA_API_KEY"),
        "gemini": ProviderInfo(id="gemini", label="Google", key_env="GEMINI_API_KEY"),
    }
    models = []
    for model_id, paper in PAPER.items():
        provider = model_id.split("/")[0]
        prefix = next((p for p in FAMILY if model_id.startswith(p)), None)
        models.append(
            ModelInfo(
                id=model_id,
                provider=provider,
                name=model_id,
                family=FAMILY[prefix] if prefix else provider,
                skills={t: paper["strength"] for t in TASKS},
                free_rpd=1000,
                context_window=128_000,
                **paper,
            )
        )
    return Registry(providers, models, env=KEYS)


def model_lists(request: httpx.Request) -> httpx.Response:
    """The providers' live model lists: Gemini 2.5 is gone."""
    host = request.url.host
    if "groq" in host:
        ids = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})
    if "nvidia" in host:
        return httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-3-super-120b-a12b"}]})
    if "googleapis" in host:
        listed = {
            "name": "models/gemini-3-flash",
            "supportedGenerationMethods": ["generateContent"],
        }
        return httpx.Response(200, json={"models": [listed]})
    return httpx.Response(404)


class FakeProviders:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def stream(self, model: ModelInfo, messages: Any, **kwargs: Any):
        self.calls.append(model.id)
        timing = TIMING[model.id]
        if timing == NOT_FOUND:
            await asyncio.sleep(0.2)
            raise ProviderError("not_found", f"models/{model.id} is not found")
        first, total = timing
        purpose = kwargs.get("purpose") or "draft"
        prompt = str(messages[-1]["content"])
        if purpose == "judge":
            count = len(re.findall(r'<candidate id="\d+">', prompt)) or 1
            grades = [{"id": i, "score": 8, "issues": []} for i in range(1, count + 1)]
            text = json.dumps({"grades": grades})
            total = first + (total - first) * 0.15  # a short reply
        else:
            text = f"{purpose.capitalize()} from {model.id}. " + "Some useful words here. " * 40
            if "palindrome" in prompt.lower():
                text += CODE
        await asyncio.sleep(first)
        step = max(1, len(text) // 10)
        chunks = [text[i : i + step] for i in range(0, len(text), step)]
        for chunk in chunks:
            yield ("answer", chunk)
            await asyncio.sleep((total - first) / len(chunks))
        if kwargs.get("meta") is not None:
            kwargs["meta"]["finish_reason"] = "stop"


def settings(budget: float) -> Settings:
    values: dict[str, Any] = {
        "time_budget_s": budget,
        "sync_interval_s": 0,
        "local_first": "off",
        "sandbox": "off",
        "laya": "off",
        "embeddings": "off",
    }
    fields = getattr(Settings, "model_fields", None) or getattr(
        Settings, "__dataclass_fields__", {}
    )
    return Settings(**{k: v for k, v in values.items() if k in fields})


async def one(engine: Engine, mode: str, question: str) -> dict[str, Any]:
    result = await engine.complete(
        [{"role": "user", "content": question}], engine.options(mode=mode)
    )

    def at(kind: str) -> float | None:
        return next((e.t for e in result.events if e.type == kind), None)

    ready = at("answer_ready")
    return {
        "first": at("answer_delta"),
        "shown": ready if ready is not None else at("answer_final"),
        "done": result.events[-1].t if result.events else None,
        "answered": bool(result.text) and not result.error,
        "error": result.error,
        "model": result.model,
        "notes": [e.text for e in result.events if e.type in ("budget", "note") and e.text],
    }


async def run(modes: list[str], budget: float) -> dict[str, Any]:
    out: dict[str, Any] = {"code": os.path.dirname(os.path.dirname(tempo.__file__)), "modes": {}}
    for mode in modes:
        fake = FakeProviders()
        engine = Engine(
            registry(),
            lambda model, f=fake: f,
            settings=settings(budget),
            health=HealthTracker(),
            clock=asyncio.get_running_loop().time,
        )
        engine._http_transport = httpx.MockTransport(model_lists)  # read by the new code only
        runs = []
        for round_ in range(1, ROUNDS + 1):
            for name, question in QUESTIONS.items():
                runs.append(
                    {"question": name, "round": round_, **await one(engine, mode, question)}
                )
        out["modes"][mode] = {
            "runs": runs,
            "calls": len(fake.calls),
            "not_found_calls": sum(c.startswith("gemini/gemini-2.5") for c in fake.calls),
        }
    return out


def median(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.median(known) if known else None


def table(result: dict[str, Any], budget: float) -> str:
    def f(value: float | None) -> str:
        return "–" if value is None else f"{value:.1f}"

    lines = [
        "| Mode | First token | On screen | On screen, slowest | Done | Done, slowest "
        f"| Answered | Past {budget:g} s | 'Not found' calls |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, data in result["modes"].items():
        runs = data["runs"]
        shown = [r["shown"] for r in runs]
        done = [r["done"] for r in runs]
        lines.append(
            f"| {mode} | {f(median([r['first'] for r in runs]))} | {f(median(shown))} "
            f"| {f(max((s for s in shown if s is not None), default=None))} | {f(median(done))} "
            f"| {f(max(d for d in done if d is not None))} "
            f"| {sum(r['answered'] for r in runs)}/{len(runs)} "
            f"| {sum((d or 0) > budget + 0.05 for d in done)} | {data['not_found_calls']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--budget", type=float, default=60.0, help="time budget in seconds")
    parser.add_argument("--modes", default="fast,auto,best")
    parser.add_argument("--json", action="store_true", help="every run, as JSON")
    args = parser.parse_args()
    loop = VirtualLoop()
    try:
        result = loop.run_until_complete(run(args.modes.split(","), args.budget))
    finally:
        loop.close()
    if args.json:
        json.dump(result, sys.stdout, indent=1)
    else:
        print(f"code: {result['code']}  (median seconds over {ROUNDS} rounds of 3 questions)")
        print(table(result, args.budget))


if __name__ == "__main__":
    main()
