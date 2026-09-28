"""The model registry: which models exist, what they are good at, and which are usable now."""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping
from importlib import resources
from pathlib import Path

import httpx
import yaml

from tempo.types import TASKS, ModelInfo, ProviderInfo

log = logging.getLogger(__name__)

OLLAMA_DEFAULT_BASE = "http://localhost:11434"


class Registry:
    def __init__(
        self,
        providers: dict[str, ProviderInfo],
        models: list[ModelInfo],
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.providers = providers
        self._models: dict[str, ModelInfo] = {m.id: m for m in models}
        self._env = os.environ if env is None else env
        for model in models:
            if model.provider not in providers:
                raise ValueError(f"Model {model.id} uses unknown provider {model.provider!r}")

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        env: Mapping[str, str] | None = None,
        include_mock: bool = False,
    ) -> Registry:
        if path is None:
            text = resources.files("tempo").joinpath("models.yaml").read_text(encoding="utf-8")
        else:
            text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        providers = {
            pid: ProviderInfo(id=pid, **(info or {}))
            for pid, info in (data.get("providers") or {}).items()
        }
        models = [ModelInfo(**m) for m in data.get("models") or []]
        if include_mock:
            from tempo.mock import MOCK_MODELS, MOCK_PROVIDER

            providers[MOCK_PROVIDER.id] = MOCK_PROVIDER
            models.extend(MOCK_MODELS)
        return cls(providers, models, env)

    # --- lookups -------------------------------------------------------------

    def all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def is_configured(self, provider_id: str) -> bool:
        provider = self.providers[provider_id]
        if provider.id == "mock":
            return True
        if provider.key_env:
            return bool(self._env.get(provider.key_env, "").strip())
        if provider.base_env:
            return bool(self._env.get(provider.base_env, "").strip())
        return False

    def credentials(self, provider_id: str) -> dict[str, str]:
        """Keyword arguments (api_key / api_base) for calling this provider."""
        provider = self.providers[provider_id]
        creds: dict[str, str] = {}
        if provider.key_env and self._env.get(provider.key_env, "").strip():
            creds["api_key"] = self._env[provider.key_env].strip()
        if provider.base_env and self._env.get(provider.base_env, "").strip():
            creds["api_base"] = self._env[provider.base_env].strip().rstrip("/")
        return creds

    # --- Ollama discovery ----------------------------------------------------

    async def discover_ollama(self, transport: httpx.AsyncBaseTransport | None = None) -> int:
        """Ask the local Ollama server which models are installed and register them.

        Returns the number of installed chat models found. Seeded Ollama models that are
        not installed are marked ``installed=False`` so the router skips them.
        """
        provider = self.providers.get("ollama")
        if provider is None or not self.is_configured("ollama"):
            return 0
        base = self.credentials("ollama").get("api_base", OLLAMA_DEFAULT_BASE)
        try:
            async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
                response = await client.get(f"{base}/api/tags")
                response.raise_for_status()
                tags = response.json().get("models") or []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Ollama discovery at %s failed: %s", base, exc)
            return 0

        installed: set[str] = set()
        for tag in tags:
            name = _strip_latest(str(tag.get("name") or tag.get("model") or ""))
            if not name or "embed" in name.lower():
                continue
            model_id = f"ollama_chat/{name}"
            installed.add(model_id)
            if model_id not in self._models:
                details = tag.get("details") or {}
                self._models[model_id] = _ollama_model_from_tag(model_id, name, details)

        for model in self._models.values():
            if model.provider == "ollama":
                model.installed = model.id in installed
        return len(installed)


def _strip_latest(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _parse_param_billions(text: str | None) -> float | None:
    match = re.match(r"^\s*([\d.]+)\s*([BbMm])", text or "")
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2).lower() == "m" else value


def _ollama_model_from_tag(model_id: str, name: str, details: Mapping) -> ModelInfo:
    """Build a registry entry for a model we have no hand-written priors for."""
    params_b = _parse_param_billions(details.get("parameter_size")) or 7.0
    # ~0.15 for 1B, ~0.5 for 8B, capped at 0.8; a rough prior until evals exist.
    strength = round(min(0.8, max(0.15, 0.15 + 0.12 * math.log2(max(params_b, 1.0)))), 2)
    general = {"chat", "writing", "summarize"}
    skills = {
        task: round(min(0.9, strength + (0.1 if task in general else 0.0)), 2) for task in TASKS
    }
    return ModelInfo(
        id=model_id,
        provider="ollama",
        name=f"{name} (local)",
        family=str(details.get("family") or "unknown"),
        context_window=4096,
        strength=strength,
        ttft_ms=800,
        tokens_per_sec=max(5.0, 120.0 / max(params_b, 1.0) * 3),
        skills=skills,
        installed=True,
    )
