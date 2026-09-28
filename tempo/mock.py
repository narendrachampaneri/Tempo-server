"""Offline demo models (enabled with TEMPO_ENABLE_MOCK=1). No network, no keys.

``mock/flaky`` has the highest priors so the router picks it first, and it always fails
with a rate-limit error. That makes the automatic fallback visible in the thinking window.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from tempo.analyzer import message_text
from tempo.providers import Delta, ProviderError
from tempo.types import TASKS, ModelInfo, ProviderInfo

MOCK_PROVIDER = ProviderInfo(id="mock", label="Mock (offline demo)")


def _mock(model_id: str, name: str, strength: float, tps: float, ttft: int) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider="mock",
        name=name,
        family="mock",
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


class MockBackend:
    def __init__(self, delay: float = 0.015) -> None:
        self.delay = delay

    async def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[Delta]:
        await asyncio.sleep(self.delay * 5)
        if model.id == "mock/flaky":
            raise ProviderError("rate_limit", "429: demo rate limit", retry_after=10)

        question = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                question = message_text(message.get("content")).strip()
                break
        if model.id == "mock/smart":
            for word in "Checking what the question asks for before answering.".split(" "):
                await asyncio.sleep(self.delay)
                yield ("reasoning", word + " ")

        answer = (
            f"This is an offline demo answer from **{model.name}**. Tempo analyzed your "
            "question, ranked the available models, and routed it here.\n\n"
            f"You asked: “{question[:300]}”\n\n"
            "Add a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a "
            "local Ollama server to get real answers."
        )
        for index, word in enumerate(answer.split(" ")):
            await asyncio.sleep(self.delay)
            yield ("answer", word if index == 0 else " " + word)
