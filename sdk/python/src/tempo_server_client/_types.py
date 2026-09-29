from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


class TempoError(Exception):
    """An error answer from Tempo-server (status, the server's code, and Retry-After seconds
    when the free quota is used up)."""

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after


@dataclass
class Event:
    """One thinking-window event: ``type`` (received, analyze, plan, stage_start, call_start,
    answer_delta, check, fallback, note, done, error, ...) and its fields in ``data``."""

    type: str
    data: dict[str, Any]

    @property
    def text(self) -> str | None:
        """The one-line summary the thinking window shows (None for answer text pieces)."""
        return self.data.get("text")

    @property
    def t(self) -> float | None:
        """Seconds since the question was received."""
        return self.data.get("t")

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class Answer:
    """The final answer, folded from the event stream."""

    text: str = ""
    reasoning: str = ""
    model: str | None = None
    question_id: str | None = None
    stages: int = 0
    requests: int = 0
    stop_reason: str | None = None
    score: float | None = None
    error: str | None = None
    events: list[Event] = field(default_factory=list)

    @property
    def trace(self) -> list[str]:
        """The thinking window as lines of text."""
        return [e.text for e in self.events if e.text]

    def apply(self, event: Event) -> None:
        self.events.append(event)
        d = event.data
        if event.type == "answer_delta":
            self.text += d.get("delta", "")
        elif event.type == "reasoning_delta":
            self.reasoning += d.get("delta", "")
        elif event.type == "answer_reset":
            self.text = ""
            self.reasoning = ""
        elif event.type == "answer_final":
            self.text = d.get("answer", self.text)
            self.score = d.get("score")
        elif event.type == "received":
            self.question_id = d.get("question_id")
        elif event.type == "done":
            self.model = d.get("model")
            self.stages = d.get("stages", 0)
            self.requests = d.get("requests", 0)
            self.stop_reason = d.get("stop_reason")
            self.question_id = d.get("question_id") or self.question_id
        elif event.type == "error":
            self.error = d.get("message")


def parse_line(line: str) -> Event | None:
    """One server-sent-events line (``data: {...}``) to an Event, or None."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    data = json.loads(payload)
    return Event(type=data.get("type", ""), data=data)


def parse_sse(lines: Iterable[str]) -> Iterator[Event]:
    for line in lines:
        event = parse_line(line)
        if event is not None:
            yield event


def ask_body(
    prompt: str | None,
    messages: list[dict[str, Any]] | None,
    mode: str,
    privacy: str,
    model: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    if prompt is None and not messages:
        raise ValueError("give a prompt or messages")
    if privacy not in ("default", "local_only", "no_logging"):
        raise ValueError("privacy must be default, local_only or no_logging")
    body: dict[str, Any] = {"mode": mode, "privacy": privacy}
    if prompt is not None:
        body["prompt"] = prompt
    if messages:
        body["messages"] = messages
    if model:
        body["model"] = model
    body.update({k: v for k, v in options.items() if v is not None})
    return body


def error_from(status: int, body: bytes, headers: Any) -> TempoError:
    message, code = f"HTTP {status}", None
    try:
        data = json.loads(body)
        err = data.get("error", data)
        if isinstance(err, dict):
            message = err.get("message") or message
            code = err.get("code")
        elif isinstance(err, str):
            message = err
        elif isinstance(data.get("detail"), str):
            message = data["detail"]
    except (ValueError, AttributeError):
        pass
    retry = headers.get("retry-after") if headers is not None else None
    return TempoError(
        message, status=status, code=code, retry_after=float(retry) if retry else None
    )
