"""Provider layer: streams chat completions and turns provider failures into ProviderError."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Literal, Protocol

from tempo.registry import Registry
from tempo.types import Access, ModelInfo

# "tool_calls" carries the complete OpenAI tool calls as a JSON list, once, at the end.
DeltaKind = Literal["answer", "reasoning", "tool_calls"]
Delta = tuple[DeltaKind, str]

ErrorKind = Literal[
    "rate_limit",
    "auth",
    "not_found",
    "timeout",
    "unavailable",
    "context",
    "bad_request",
    "empty",
    "invalid",
    "unknown",
]

ERROR_LABELS: dict[str, str] = {
    "rate_limit": "rate limited",
    "auth": "API key rejected",
    "not_found": "model not found",
    "timeout": "timed out",
    "unavailable": "provider unavailable",
    "context": "input too long for this model",
    "bad_request": "request rejected",
    "empty": "returned an empty answer",
    "invalid": "reply did not match the request (tool call or JSON schema)",
    "unknown": "failed",
}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


class ChatBackend(Protocol):
    """Streams one completion.

    ``access`` selects whose keys to use, ``meta`` is filled with what the provider reported
    (``headers``, ``finish_reason``), and ``purpose`` names the stage job (draft, judge, fix,
    merge, ...); real providers ignore it, test and demo backends use it to shape replies.
    """

    def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        access: Access | None = None,
        meta: dict[str, Any] | None = None,
        purpose: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        parallel_tool_calls: bool | None = None,
    ) -> AsyncIterator[Delta]: ...


class ThinkTagSplitter:
    """Separates ``<think>...</think>`` blocks (emitted inline by some open models) from the
    answer. Tags may be split across streamed chunks."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._thinking = False
        self._strip_next = False

    def feed(self, text: str) -> list[Delta]:
        self._buffer += text
        out: list[Delta] = []
        while True:
            tag = self.CLOSE if self._thinking else self.OPEN
            index = self._buffer.find(tag)
            if index >= 0:
                self._emit(out, self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._thinking = not self._thinking
                self._strip_next = not self._thinking
                continue
            keep = _partial_suffix(self._buffer, tag)
            self._emit(out, self._buffer[: len(self._buffer) - keep])
            self._buffer = self._buffer[len(self._buffer) - keep :]
            return out

    def flush(self) -> list[Delta]:
        out: list[Delta] = []
        self._emit(out, self._buffer)
        self._buffer = ""
        return out

    def _emit(self, out: list[Delta], text: str) -> None:
        if not self._thinking and self._strip_next:
            text = text.lstrip()
            if text:
                self._strip_next = False
        if text:
            out.append(("reasoning" if self._thinking else "answer", text))


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``."""
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


# How providers word a rejected key. LiteLLM raises some of these as BadRequestError (seen from
# Groq: "Invalid API Key", Cerebras: "Wrong API Key"), which must still cool the provider down.
KEY_REJECTED = re.compile(
    r"invalid[ _]api[ _]key|wrong api key|incorrect api key|api key not valid|api key expired|"
    r"missing authentication|no auth credentials|invalid x-api-key|invalid authentication",
    re.IGNORECASE,
)


def classify_exception(exc: BaseException) -> ProviderError:
    import litellm

    message = " ".join(str(exc).split())[:300] or type(exc).__name__
    if KEY_REJECTED.search(message) and not isinstance(exc, litellm.RateLimitError):
        return ProviderError("auth", message)
    checks: list[tuple[type[BaseException], ErrorKind]] = [
        (litellm.ContextWindowExceededError, "context"),
        (litellm.RateLimitError, "rate_limit"),
        (litellm.AuthenticationError, "auth"),
        (litellm.PermissionDeniedError, "auth"),
        (litellm.NotFoundError, "not_found"),
        (litellm.Timeout, "timeout"),
        (litellm.APIConnectionError, "unavailable"),
        (litellm.ServiceUnavailableError, "unavailable"),
        (litellm.InternalServerError, "unavailable"),
        (litellm.BadRequestError, "bad_request"),
    ]
    for exc_type, kind in checks:
        if isinstance(exc, exc_type):
            return ProviderError(kind, message, _retry_after(exc) if kind == "rate_limit" else None)

    status = getattr(exc, "status_code", None)
    if status == 429:
        return ProviderError("rate_limit", message, _retry_after(exc))
    if status in (401, 403):
        return ProviderError("auth", message)
    if status == 404:
        return ProviderError("not_found", message)
    if isinstance(status, int) and status >= 500:
        return ProviderError("unavailable", message)
    if isinstance(exc, TimeoutError):
        return ProviderError("timeout", message)
    return ProviderError("unknown", message)


def _retry_after(exc: BaseException) -> float | None:
    """Seconds from a Retry-After header, wherever this exception keeps its headers."""
    sources = (
        getattr(exc, "headers", None),
        getattr(exc, "litellm_response_headers", None),  # where LiteLLM keeps provider headers
        getattr(getattr(exc, "response", None), "headers", None),
    )
    for headers in sources:
        if not headers:
            continue
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _add_tool_piece(calls: dict[int, dict[str, Any]], piece: Any) -> None:
    """Join a streamed tool-call piece (id and name first, arguments in fragments) into calls."""
    get = piece.get if isinstance(piece, dict) else lambda k, d=None: getattr(piece, k, d)
    index = get("index", None)
    index = len(calls) if index is None else int(index)
    call = calls.setdefault(
        index, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}}
    )
    if get("id", None):
        call["id"] = get("id")
    fn = get("function", None)
    if fn is not None:
        fget = fn.get if isinstance(fn, dict) else lambda k, d=None: getattr(fn, k, d)
        if fget("name", None):
            call["function"]["name"] = fget("name")
        if fget("arguments", None):
            call["function"]["arguments"] += fget("arguments")


def _load_litellm() -> Any:
    import litellm

    litellm.suppress_debug_info = True
    litellm.drop_params = True  # drop parameters a provider does not support
    return litellm


class LiteLLMBackend:
    """Calls any provider LiteLLM supports, using the credentials from the registry."""

    def __init__(self, registry: Registry, timeout: float = 60.0) -> None:
        self.registry = registry
        self.timeout = timeout

    async def warm_up(self) -> None:
        """Import LiteLLM before the first request; the import alone takes a few seconds."""
        if any(self.registry.is_configured(p) for p in self.registry.providers if p != "mock"):
            await asyncio.to_thread(_load_litellm)

    async def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        access: Access | None = None,
        meta: dict[str, Any] | None = None,
        purpose: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        parallel_tool_calls: bool | None = None,
    ) -> AsyncIterator[Delta]:
        litellm = _load_litellm()
        meta = meta if meta is not None else {}
        kwargs: dict[str, Any] = {
            "model": self.registry.litellm_model(model),
            "messages": list(messages),
            "stream": True,
            "timeout": self.timeout,
            "num_retries": 0,  # Tempo falls back to other models instead of retrying
            **self.registry.credentials(model.provider, access),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:  # only for models with native tool support; the others get them emulated
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            if parallel_tool_calls is not None:
                kwargs["parallel_tool_calls"] = parallel_tool_calls

        splitter = ThinkTagSplitter()
        calls: dict[int, dict[str, Any]] = {}  # streamed tool-call pieces, by index
        try:
            response = await litellm.acompletion(**kwargs)
            hidden = getattr(response, "_hidden_params", None) or {}
            meta["headers"] = hidden.get("additional_headers") or {}
            async for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                if choices[0].finish_reason:
                    meta["finish_reason"] = choices[0].finish_reason
                delta = choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ("reasoning", reasoning)
                content = getattr(delta, "content", None)
                if content:
                    for item in splitter.feed(content):
                        yield item
                for piece in getattr(delta, "tool_calls", None) or []:
                    _add_tool_piece(calls, piece)
            for item in splitter.flush():
                yield item
            if calls:
                yield ("tool_calls", json.dumps([calls[i] for i in sorted(calls)]))
        except ProviderError:
            raise
        except Exception as exc:
            meta["headers"] = getattr(exc, "litellm_response_headers", None) or {}
            raise classify_exception(exc) from exc
