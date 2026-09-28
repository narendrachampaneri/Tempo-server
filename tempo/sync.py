"""Registry auto-sync and health checks, using each provider's model-list endpoint.

Listing models costs no generation quota, so it doubles as a health check: a 401/403 means the
key is bad (the whole provider cools down), a timeout or 5xx is reported as degraded. Models a
provider no longer lists are marked ``listed=False`` and skipped by the router; new free models
are added with priors guessed from their name and size until `tempo eval` measures them.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from tempo.health import HealthTracker
from tempo.registry import Registry
from tempo.types import TASKS, ModelInfo

log = logging.getLogger(__name__)

DEFAULT_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "openrouter": "https://openrouter.ai/api/v1",
}
# Model ids that are not chat models (speech, images, embeddings, guards...).
NOT_CHAT = re.compile(
    r"whisper|tts|speech|audio|embed|imagen|veo|image|vision-preview|guard|safeguard|orpheus|"
    r"playai|aqa|rerank|moderation|content-safety|transcribe|compound|lyria|music",
    re.IGNORECASE,
)
# Not added automatically: OpenRouter's own meta-router, and "stealth" preview models, whose
# prompts are typically logged by the provider. Add them to models.yaml yourself if wanted.
SKIP_IDS = re.compile(r"^(openrouter/|stealth/)", re.IGNORECASE)
FAMILIES = (
    "gpt-oss",
    "llama",
    "qwen",
    "gemma",
    "gemini",
    "mistral",
    "mixtral",
    "codestral",
    "deepseek",
    "glm",
    "kimi",
    "nemotron",
    "phi",
    "granite",
    "minimax",
    "command",
    "hermes",
)


@dataclass
class ProviderStatus:
    provider: str
    ok: bool
    checked_at: float
    listed: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "checked_at": self.checked_at,
            "listed": self.listed,
            "added": self.added,
            "removed": self.removed,
            "error": self.error,
        }


def guess_model(model_id: str, provider: str, name: str | None, context: int | None) -> ModelInfo:
    """Priors for a model we have never measured, from its size and family."""
    low = model_id.lower()
    family = next((f for f in FAMILIES if f in low), "unknown")
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b\b", low)]
    params = max(sizes) if sizes else 30.0
    active = re.search(r"a(\d+(?:\.\d+)?)b\b", low)  # mixture-of-experts: active params
    strength = min(0.85, max(0.2, 0.15 + 0.11 * math.log2(max(params, 1.0))))
    speed = float(active.group(1)) if active else params
    general = {"chat", "writing", "summarize"}
    skills = {t: round(min(0.9, strength + (0.05 if t in general else 0.0)), 2) for t in TASKS}
    if "coder" in low or "codestral" in low:
        skills["code"] = min(0.9, strength + 0.1)
    return ModelInfo(
        id=f"{provider}/{model_id}",
        provider=provider,
        name=name or model_id,
        family=family,
        context_window=int(context or 8192),
        strength=round(strength, 2),
        reasoning=any(k in low for k in ("reason", "think", "r1", "gpt-oss", "qwq")),
        ttft_ms=800,
        tokens_per_sec=max(30.0, 3000.0 / max(speed, 1.0)),
        skills=skills,
        source="sync",
        listed=True,
    )


class RegistrySync:
    def __init__(
        self,
        registry: Registry,
        health: HealthTracker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.registry = registry
        self.health = health
        self.transport = transport
        self._clock = clock
        self.status: dict[str, ProviderStatus] = {}

    async def run(self) -> dict[str, ProviderStatus]:
        async with httpx.AsyncClient(transport=self.transport, timeout=10.0) as client:
            for provider in DEFAULT_BASES:
                if provider not in self.registry.providers:
                    continue
                if provider != "openrouter" and not self.registry.is_configured(provider):
                    continue
                self.status[provider] = await self._sync(client, provider)
        if "ollama" in self.registry.providers and self.registry.is_configured("ollama"):
            found = await self.registry.discover_ollama(transport=self.transport)
            self.status["ollama"] = ProviderStatus("ollama", found > 0, self._clock(), found)
        return self.status

    async def _sync(self, client: httpx.AsyncClient, provider: str) -> ProviderStatus:
        creds = self.registry.credentials(provider)
        base = creds.get("api_base") or DEFAULT_BASES[provider]
        headers = {}
        params: dict[str, str] = {}
        if provider == "gemini":
            params["key"] = creds.get("api_key", "")
        elif creds.get("api_key"):
            headers["Authorization"] = f"Bearer {creds['api_key']}"
        try:
            response = await client.get(f"{base}/models", headers=headers, params=params)
        except httpx.HTTPError as exc:
            return ProviderStatus(provider, False, self._clock(), error=f"unreachable: {exc}")
        if response.status_code in (401, 403):
            self._reject_key(provider)
            return ProviderStatus(provider, False, self._clock(), error="API key rejected")
        if response.status_code >= 400:
            return ProviderStatus(
                provider, False, self._clock(), error=f"HTTP {response.status_code}"
            )
        try:
            listed = self._parse(provider, response.json())
        except (ValueError, KeyError, TypeError) as exc:
            return ProviderStatus(provider, False, self._clock(), error=f"bad model list: {exc}")
        added, removed = self._apply(provider, listed)
        return ProviderStatus(provider, True, self._clock(), len(listed), added, removed)

    def _parse(self, provider: str, data: Any) -> dict[str, ModelInfo]:
        out: dict[str, ModelInfo] = {}
        if provider == "gemini":
            for item in data.get("models", []):
                if "generateContent" not in item.get("supportedGenerationMethods", []):
                    continue
                raw = str(item["name"]).removeprefix("models/")
                if NOT_CHAT.search(raw):
                    continue
                model = guess_model(
                    raw, provider, item.get("displayName"), item.get("inputTokenLimit")
                )
                out[model.id] = model
            return out
        for item in data.get("data", []):
            raw = str(item["id"])
            if NOT_CHAT.search(raw) or SKIP_IDS.search(raw) or item.get("active") is False:
                continue
            if provider == "openrouter":
                pricing = item.get("pricing") or {}
                if str(pricing.get("prompt")) != "0" or str(pricing.get("completion")) != "0":
                    continue  # only free models
            context = item.get("context_length") or item.get("context_window")
            model = guess_model(raw, provider, item.get("name"), context)
            out[model.id] = model
        return out

    def _apply(self, provider: str, listed: dict[str, ModelInfo]) -> tuple[list[str], list[str]]:
        added, removed = [], []
        for model in self.registry.all():
            if model.provider != provider:
                continue
            was = model.listed
            model.listed = model.id in listed
            if was is not False and not model.listed:
                removed.append(model.id)
        for model_id, model in listed.items():
            if self.registry.get(model_id) is None:
                self.registry.add(model)
                added.append(model_id)
        if added or removed:
            log.info("%s: %d new models, %d no longer listed", provider, len(added), len(removed))
        return added, removed

    def _reject_key(self, provider: str) -> None:
        if self.health is None:
            return
        for model in self.registry.all():
            if model.provider == provider:
                self.health.record_failure(model, "auth")
                break
