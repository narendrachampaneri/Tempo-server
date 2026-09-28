"""Provider layer: streams chat completions and turns provider failures into ProviderError."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Literal, Protocol

from tempo.registry import Registry
from tempo.types import ModelInfo

DeltaKind = Literal["answer", "reasoning"]
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
    "unknown": "failed",
}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


class ChatBackend(Protocol):
    def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
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


def classify_exception(exc: BaseException) -> ProviderError:
    import litellm

    message = " ".join(str(exc).split())[:300] or type(exc).__name__
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
    ) -> AsyncIterator[Delta]:
        litellm = _load_litellm()
        kwargs: dict[str, Any] = {
            "model": model.id,
            "messages": list(messages),
            "stream": True,
            "timeout": self.timeout,
            "num_retries": 0,  # Tempo falls back to other models instead of retrying
            **self.registry.credentials(model.provider),
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        splitter = ThinkTagSplitter()
        try:
            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ("reasoning", reasoning)
                content = getattr(delta, "content", None)
                if content:
                    for item in splitter.feed(content):
                        yield item
            for item in splitter.flush():
                yield item
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_exception(exc) from exc
