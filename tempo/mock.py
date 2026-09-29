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

from tempo import compat
from tempo.analyzer import message_text
from tempo.providers import Delta, ProviderError
from tempo.types import TASKS, ModelInfo, ProviderInfo

MOCK_PROVIDER = ProviderInfo(id="mock", label="Mock (offline demo)", training_on_outputs="yes")
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
        # The demo text is written by Tempo-server's own code, so it is under its licence.
        licence="Apache-2.0",
    )


MOCK_MODELS = [
    _mock("mock/flaky", "Flaky Demo Model", strength=0.95, tps=400, ttft=200),
    _mock("mock/smart", "Smart Demo Model", strength=0.80, tps=300, ttft=300),
    _mock("mock/fast", "Fast Demo Model", strength=0.40, tps=900, ttft=100),
]
# The smart one has native tools and vision; the fast one gets tools emulated in its prompt.
for _model in MOCK_MODELS:
    _model.tools = _model.id != "mock/fast"
    _model.vision = _model.id != "mock/fast"


def _last_user(messages: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message_text(message.get("content")).strip()
    return ""


def _system(messages: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join(message_text(m.get("content")) for m in messages if m.get("role") == "system")


def _json_reply(system: str) -> str | None:
    """A demo answer for strict-JSON requests: a value that matches the schema."""
    if compat.JSON_MARK not in system:
        return None
    after = system.split(compat.JSON_MARK, 1)[1]
    schema_text = after.split("\n", 1)[1] if "\n" in after else ""
    try:
        schema = json.loads(schema_text) if schema_text.strip() else None
    except ValueError:
        schema = None
    value = compat.example_for(schema) if schema else {"answer": "offline demo"}
    return json.dumps(value)


def _emulated_tool_reply(system: str, messages: Sequence[Mapping[str, Any]]) -> str | None:
    """A demo reply for a model whose tools are described in its prompt."""
    if compat.EMULATION_MARK not in system:
        return None
    last = messages[-1] if messages else {}
    if message_text(last.get("content")).startswith("Result of "):
        return (
            "Offline demo answer built from the tool result: "
            + message_text(last.get("content")).split("\n", 1)[-1][:200]
        )
    listing = system.split("You can call these functions:\n", 1)[1].split("\n\n", 1)[0]
    tools = json.loads(listing)
    required = re.search(r"You must call the function '([^']+)'", system)
    tool = next((t for t in tools if required and t["name"] == required.group(1)), tools[0])
    body = {"name": tool["name"], "arguments": compat.example_for(tool.get("parameters"))}
    return f"<tool_call>{json.dumps(body)}</tool_call>"


def demo_reply(model: ModelInfo, messages: Sequence[Mapping[str, Any]], purpose: str | None) -> str:
    text = _last_user(messages)
    if purpose == "judge":
        candidates = re.findall(r'<candidate id="\d+">(.*?)</candidate>', text, re.DOTALL)
        grades = [
            {"id": i, "score": 9, "issues": []}
            if IMPROVED in body or body.strip().startswith(("{", "["))  # JSON answers pass
            else {"id": i, "score": 6, "issues": ["could be more specific"]}
            for i, body in enumerate(candidates, 1)
        ]
        return json.dumps({"grades": grades})
    system = _system(messages)
    for special in (_json_reply(system), _emulated_tool_reply(system, messages)):
        if special is not None:
            return special
    if any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, Mapping))
        for m in messages
    ):
        return (
            f"This is an offline demo answer from **{model.name}**, a vision model: it received "
            "your image (demo models do not look at it)."
        )
    if purpose == "split":
        pieces = [p.strip() for p in re.split(r"(?<=[?.!])\s+|\n+", text) if len(p.strip()) > 3]
        return json.dumps({"parts": pieces[:6] or [text]})
    if purpose == "parts":
        part = text.rsplit("Answer only this part:", 1)[-1].strip()
        return f"Offline demo answer from **{model.name}** for this part: “{part[:120]}”."
    # Asked to write code: demo answers include a (placeholder) code block, as real ones would.
    code = (
        "\n\n```python\ndef demo():\n    return 'offline demo'\n```"
        if _WANTS_CODE.search(text)
        else ""
    )
    if purpose in ("fix", "merge", "polish", "combine"):
        return (
            f"This is an {IMPROVED} offline demo answer from **{model.name}** (stage job: "
            f"{purpose}). It addresses the issues the checker found in the earlier draft.{code}\n\n"
            "Add a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a "
            "local Ollama server to get real answers."
        )
    return (
        f"This is an offline demo answer from **{model.name}**. Tempo analyzed your question, "
        f"ranked the available models, and routed it here.\n\nYou asked: “{text[:300]}”\n\n"
        f"Add a free API key (for example `GROQ_API_KEY`) or point `OLLAMA_API_BASE` at a "
        f"local Ollama server to get real answers.{code}"
    )


_WANTS_CODE = re.compile(
    r"\b(write|fix|implement|convert)\b.*\b(function|code|script|class|program)\b", re.I | re.S
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
        tools = kwargs.get("tools")
        if tools and not (messages and messages[-1].get("role") == "tool"):
            # Native tool calls: call the required function (or the first) with example
            # arguments that match its schema.
            choice = kwargs.get("tool_choice")
            wanted = (
                (choice.get("function") or {}).get("name") if isinstance(choice, dict) else None
            )
            fn = next(
                (t["function"] for t in tools if t["function"]["name"] == wanted),
                tools[0]["function"],
            )
            call = compat.make_call(fn["name"], compat.example_for(fn.get("parameters")))
            yield ("tool_calls", json.dumps([call]))
            return
        if tools:
            yield ("answer", "Offline demo answer built from the tool result: ")
            yield ("answer", message_text(messages[-1].get("content"))[:200])
            return
        if model.id == "mock/smart" and purpose in (None, "draft"):
            for word in "Checking what the question asks for before answering.".split(" "):
                await asyncio.sleep(self.delay)
                yield ("reasoning", word + " ")
        reply = demo_reply(model, messages, purpose)
        if purpose in ("judge", "split") or reply.startswith(("{", "[", "<tool_call>")):
            yield ("answer", reply)
            return
        for index, word in enumerate(reply.split(" ")):
            await asyncio.sleep(self.delay)
            yield ("answer", word if index == 0 else " " + word)
