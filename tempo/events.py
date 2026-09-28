"""Trace events. Every step of a request emits one; clients render ``text`` in the
thinking window and can use the structured fields for anything else."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tempo.providers import ERROR_LABELS

# High-frequency events that carry text chunks rather than decisions.
STREAM_EVENTS = frozenset({"answer_delta", "reasoning_delta"})


@dataclass
class Event:
    type: str
    t: float
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str | None:
        return describe(self.type, self.data)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "t": self.t, **self.data}
        text = self.text
        if text:
            out["text"] = text
        return out


def _quota(rpd: int | None, local: bool) -> str:
    if local:
        return "local, unlimited"
    if rpd is None:
        return "no daily limit"
    return f"{rpd:,} req/day free"


def describe(kind: str, d: dict[str, Any]) -> str | None:
    if kind == "received":
        return f"Received · mode {d.get('mode', 'auto')}"
    if kind == "analyze":
        parts = [f"Understanding: {d['task']}", f"complexity {d['complexity']:.2f}"]
        if d.get("script") and d["script"] != "latin":
            parts.append(f"{d['script']} script")
        if d.get("needs"):
            parts.append("needs " + ", ".join(d["needs"]))
        parts.append(f"~{d['input_tokens']:,} input tokens")
        return " · ".join(parts)
    if kind == "plan":
        return (
            f"Plan: {d['strategy']} model with automatic fallback "
            f"(up to {d['max_attempts']} tries across {d['candidates']} candidates)"
        )
    if kind == "route":
        line = (
            f"Routing → {d['model']} · {d['why']} · ~{d['latency_s']:.1f}s · "
            f"{_quota(d.get('free_rpd'), d.get('local', False))}"
        )
        skipped = d.get("skipped") or {}
        if skipped:
            total = sum(skipped.values())
            detail = ", ".join(f"{n} {reason}" for reason, n in skipped.items())
            line += f" (skipped {total}: {detail})"
        return line
    if kind == "call_start":
        return f"Calling {d['model']} (attempt {d['attempt']})"
    if kind == "call_error":
        return f"✗ {d['model']}: {ERROR_LABELS.get(d['kind'], d['kind'])}"
    if kind == "answer_reset":
        return "Discarding the partial answer"
    if kind == "fallback":
        return f"Falling back → {d['to']}"
    if kind == "call_end":
        ttft = d.get("ttft_ms")
        first = f" (first token {ttft / 1000:.1f}s)" if ttft is not None else ""
        return f"Answer from {d['model']} in {d['ms'] / 1000:.1f}s{first}"
    if kind == "done":
        calls = d["attempts"]
        plural = "s" if calls != 1 else ""
        return f"Done in {d['total_ms'] / 1000:.1f}s · {calls} model call{plural}"
    if kind == "error":
        return f"Error: {d['message']}"
    return None
