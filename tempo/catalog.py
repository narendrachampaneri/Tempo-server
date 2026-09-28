"""The live free-model catalog: one row per model, as `tempo models --free` shows it."""

from __future__ import annotations

import time
from typing import Any

from tempo.health import HealthTracker
from tempo.registry import Registry, expired
from tempo.types import FLAGGED_POLICIES, ModelInfo


def _n(value: int) -> str:
    return f"{value // 1000:,}K" if value >= 10_000 and value % 1000 == 0 else f"{value:,}"


def limits_text(registry: Registry, model: ModelInfo) -> str:
    provider = registry.providers[model.provider]
    if provider.local:
        return "local: none"
    parts = []
    if model.free_rpm:
        parts.append(f"{_n(model.free_rpm)}/min")
    if model.free_rpd:
        parts.append(f"{_n(model.free_rpd)}/day")
    if model.free_tpm:
        parts.append(f"{_n(model.free_tpm)} tok/min")
    if model.free_tpd:
        parts.append(f"{_n(model.free_tpd)} tok/day")
    shared = []
    if provider.shared_rpm:
        shared.append(f"{_n(provider.shared_rpm)}/min")
    if provider.shared_rpd:
        shared.append(f"{_n(provider.shared_rpd)}/day")
    if provider.shared_rpmonth:
        shared.append(f"{_n(provider.shared_rpmonth)}/month")
    if shared:
        parts.append(" ".join(shared) + " shared")
    if not parts and provider.free_limit_note:
        return provider.free_limit_note.split(";")[0].split(",")[0]
    return " · ".join(parts) or "-"


def health_text(model: ModelInfo, health: HealthTracker | None) -> str:
    reason = health.unavailable_reason(model) if health is not None else None
    if reason:
        return reason
    if model.fallback_only:
        return "last fallback"
    if model.endpoints == 0:
        return "no endpoints"
    if model.endpoints is not None:
        up = f" {model.uptime_30m:.1f}%" if model.uptime_30m is not None else ""
        return ("degraded" if model.degraded else "ok") + up
    if model.listed is True:
        return "listed"
    if model.listed is False:
        return "no longer offered"
    return "not checked"


def status_text(registry: Registry, model: ModelInfo) -> str:
    provider = registry.providers[model.provider]
    if not registry.is_enabled(model.provider):
        return "off by default"
    if provider.byok_only:
        return "your own key"
    if not registry.is_configured(model.provider):
        return "Ollama not set" if provider.local else "needs key"
    if model.installed is False:
        return "not installed"
    return "ready" if model.chat_capable else "ready (not chat)"


def rows(
    registry: Registry,
    health: HealthTracker | None = None,
    checked: dict[str, float] | None = None,
    everything: bool = False,
) -> list[dict[str, Any]]:
    """Free models, live facts first. ``checked``: when each provider's list was last read."""
    out = []
    for m in sorted(registry.all(), key=lambda m: (m.provider, m.type != "chat", m.id)):
        if m.provider == "mock":
            continue
        if not everything and (m.listed is False or expired(m.expires)):
            continue
        provider = registry.providers[m.provider]
        when = m.health_checked or (checked or {}).get(m.provider)
        policy = registry.data_policy(m)
        out.append(
            {
                "provider": provider.id,
                "model": m.id,
                "type": m.type,
                "context": m.context_window if m.context_known else None,
                "max_output": m.max_output,
                "inputs": m.inputs,
                "tools": m.tools,
                "limits": limits_text(registry, m),
                "data_policy": policy,
                "flagged": policy in FLAGGED_POLICIES,
                "training": registry.training_verdict(m),
                "health": health_text(m, health),
                "last_check": when,
                "status": status_text(registry, m),
                "preview": m.preview,
                "domain": m.domain,
                "expires": m.expires,
                "source": m.source,
            }
        )
    return out


def when_text(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "-"
    age = (now or time.time()) - ts
    if age < 90:
        return "just now"
    if age < 3600 * 2:
        return f"{age / 60:.0f} min ago"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))
