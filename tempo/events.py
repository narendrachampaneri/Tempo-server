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


STOP_REASONS = {
    "passed": "answer passed its check",
    "decided": "decided the answer is good enough",
    "not_passed": "no answer passed its check; sent the best one",
    "polished": "final rewrite on the last stage",
    "unchecked": "no stage left to check the answer",
    "cache": "answered from cache",
    "budget_stages": "stage budget used",
    "budget_time": "time budget reached",
    "budget_quota": "free-quota budget used",
}


def _quota_note(quota: dict[str, Any] | None) -> str:
    notes = []
    for model, left in (quota or {}).items():
        if left.get("rpd") is not None:
            notes.append(f"{model} {left['rpd']:,}/day left")
    return " · ".join(notes[:2])


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def describe(kind: str, d: dict[str, Any]) -> str | None:
    if kind == "note":  # a plain message for the thinking window
        return str(d.get("message", ""))
    if kind == "sandbox":  # code or a calculation was run in the sandbox (tempo/execute.py)
        mark = {"passed": "✓", "failed": "✗"}.get(d.get("status", ""), "·")
        return f"{mark} {d.get('detail', '')}"
    if kind == "received":
        return f"Received · mode {d.get('mode', 'auto')}"
    if kind == "cache_hit":
        minutes = max(1, round(d.get("age_s", 0) / 60))
        return (
            f"Answered from cache (similarity {d['similarity']:.2f}, {minutes} min old, "
            f"first answered by {d['model']})"
        )
    if kind == "analyze":
        parts = [f"Understanding: {d['task']}", f"complexity {d['complexity']:.2f}"]
        if d.get("script") and d["script"] != "latin":
            parts.append(f"{d['script']} script")
        if d.get("needs"):
            parts.append("needs " + ", ".join(d["needs"]))
        parts.append(f"~{d['input_tokens']:,} input tokens")
        if d.get("source") and d["source"] != "rules":
            parts.append(f"via {d['source']}")
        return " · ".join(parts)
    if kind == "decision":
        laya = d.get("laya")
        if d.get("laya_status") != "ok":
            return f"Decision {d['name']}: {d['value']} (rules) · Laya {d.get('laya_status')}" + (
                f" after {d['laya_ms']:.0f} ms" if d.get("laya_ms") else ""
            )
        who = "Laya decides" if d.get("used") == "laya" else "rules decide (Laya in shadow)"
        return (
            f"Decision {d['name']}: {d['value']} · Laya: {laya} "
            f"(p {d.get('laya_p', 0):.2f}, {d.get('laya_ms', 0):.0f} ms) · rules: "
            f"{d.get('rules')} · {who}"
        )
    if kind == "plan":
        line = (
            f"Plan: {d['strategy']} · up to {_plural(d['max_stages'], 'stage')} · "
            f"{d['time_budget_s']:g}s · {_plural(d['quota_budget'], 'free request')}"
        )
        if d.get("parts"):
            line += f" · {d['parts']} parts"
        line += f" · {d['reason']}" if d.get("reason") else ""
        return line + (f" · {d['laya']}" if d.get("laya") else "")
    if kind == "stage_start":
        models = ", ".join(d.get("models") or []) or "no model"
        line = f"Stage {d['stage']}/{d['max_stages']} · {d['job']} · {models}"
        return line + (f" · {d['reason']}" if d.get("reason") else "")
    if kind == "stage_end":
        line = (
            f"Stage {d['stage']} done in {d['ms'] / 1000:.1f}s · "
            f"{_plural(d['requests_left'], 'free request')} left · {d['time_left_s']:g}s left"
        )
        note = _quota_note(d.get("quota"))
        return line + (f" · {note}" if note else "")
    if kind == "check":
        score = d.get("best_score")
        if score is None:
            return "Check: nothing to grade"
        verdict = "✓ passed" if d.get("passed") else "✗ not good enough yet"
        issues = []
        for result in d.get("results") or []:
            if result.get("score") == score:
                issues = result.get("issues") or []
                break
        line = f"Check: best score {score:.2f} {verdict}"
        if d.get("judge_model"):
            line += f" (judge {d['judge_model']})"
        return line + (f" · {'; '.join(issues[:2])}" if issues else "")
    if kind == "call_start":
        attempt = f" (attempt {d['attempt']})" if d.get("attempt", 1) > 1 else ""
        return f"Calling {d['model']}{attempt}"
    if kind == "call_error":
        return f"✗ {d['model']}: {ERROR_LABELS.get(d['kind'], d['kind'])}"
    if kind == "answer_reset":
        if str(d.get("reason", "")).startswith("replaced"):
            return f"Replacing the shown answer with stage {d['stage']}"
        return "Discarding the partial answer"
    if kind == "continue":
        same = d.get("to") == d.get("model")
        who = "continuing" if same else f"continuing with {d.get('to')}"
        return f"✂ {d.get('model')} stopped at its output limit · {who}"
    if kind == "fallback":
        return f"Falling back → {d['to']}"
    if kind == "call_end":
        ttft = d.get("ttft_ms")
        first = f" (first token {ttft / 1000:.1f}s)" if ttft is not None else ""
        return f"Answer from {d['model']} in {d['ms'] / 1000:.1f}s{first}"
    if kind == "budget":
        if d.get("has_answer", True):
            return f"Budget: {d['detail']} · using the best answer so far"
        return f"Budget: {d['detail']}"
    if kind == "answer_final":
        if d.get("cached"):
            return None
        quality = f"score {d['score']:.2f}" if d.get("score") is not None else "unchecked"
        return f"Final answer from {d['model']} (stage {d['stage']}, {quality})"
    if kind == "done":
        reason = STOP_REASONS.get(d.get("stop_reason") or "", d.get("stop_reason") or "")
        return (
            f"Done in {d['total_ms'] / 1000:.1f}s · {_plural(d['stages'], 'stage')} · "
            f"{_plural(d['attempts'], 'model call')} · {d['requests']} free used · {reason}"
        )
    if kind == "error":
        return f"Error: {d['message']}"
    return None
