"""Offline demo models (enabled with TEMPO_ENABLE_MOCK=1). No network, no keys.

They act out every stage so the whole pipeline is visible without a provider:
``mock/flaky`` has the highest priors, so the router picks it first, and it always fails with
a rate-limit error (fallback). As judge, the demo grades a first draft 6/10 and a rewritten
answer 9/10, so a question goes draft -> check -> fix -> check.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from tempo.analyzer import message_text
from tempo.providers import Delta, ProviderError
from tempo.types import TASKS, ModelInfo, ProviderInfo

MOCK_PROVIDER = ProviderInfo(id="mock", label="Mock (offline demo)")
IMPROVED = "improved"


def _mock(model_id: str, name: str, strength: float, tps: float, ttft: int) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="mock",
        name=name,
        family=model_id.split("/")[1],
        context_window=131072,
        strength=strength,
        ttft_ms=ttft,
        tokens_per_sec=tps,
        skills={task: strength for task in TASKS},
    )


MOCK_MODELS = [
    _mock("mock/flaky", "Flaky Demo Model", strength=0.95, tps=400, ttft=200),
    _mock("mock/smart", "Smart Demo Model", strength=0.80, tps=300, ttft=300),
    _mock("mock/fast", "Fast Demo Model", strength=0.40, tps=900, ttft=100),
]


def _last_user(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message_text(message.get("content")).strip()
    return ""


def demo_reply(model: ModelInfo, messages: Sequence[Mapping[str, Any]], purpose: str | None) -> str:
    text = _last_user(messages)
    if purpose == "judge":
        candidates = re.findall(r'<candidate id="\d+">(.*?)</candidate>', text, re.DOTALL)
        grades = [
            {"id": i, "score": 9, "issues": []}
            if IMPROVED in body
            else {"id": i, "score": 6, "issues": ["could be more specific"]}
            for i, body in enumerate(candidates, 1)
        ]
        return json.dumps({"grades": grades})
    if purpose == "split":
        pieces = [p.strip() for p in re.split(r"(?<=[?.!])\s+|\n+", text) if len(p.strip()) > 3]
        return json.dumps({"parts": pieces[:6] or [text]})
    if purpose == "parts":
        part = text.rsplit("Answer only this part:", 1)[-1].strip()
        return f"Offline demo answer from **{model.name}** for this part: “{part[:120]}”."
    if purpose in ("fix", "merge", "polish", "combine"):
        return (
            f"This is an {IMPROVED} offline demo answer from **{model.name}** (stage job: "
            f"{purpose}). It addresses the issues the checker found in the earlier draft.\n\n"
            "Add a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a "
            "local Ollama server to get real answers."
        )
    return (
        f"This is an offline demo answer from **{model.name}**. Tempo analyzed your question, "
        f"ranked the available models, and routed it here.\n\nYou asked: “{text[:300]}”\n\n"
        "Add a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a "
        "local Ollama server to get real answers."
    )


class MockBackend:
    def __init__(self, delay: float = 0.015) -> None:
        self.delay = delay

    async def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[Delta]:
        purpose = kwargs.get("purpose")
        await asyncio.sleep(self.delay * 5)
        if model.id == "mock/flaky":
            raise ProviderError("rate_limit", "429: demo rate limit", retry_after=10)
        if model.id == "mock/smart" and purpose in (None, "draft"):
            for word in "Checking what the question asks for before answering.".split(" "):
                await asyncio.sleep(self.delay)
                yield ("reasoning", word + " ")
        reply = demo_reply(model, messages, purpose)
        if purpose in ("judge", "split"):
            yield ("answer", reply)
            return
        for index, word in enumerate(reply.split(" ")):
            await asyncio.sleep(self.delay)
            yield ("answer", word if index == 0 else " " + word)
