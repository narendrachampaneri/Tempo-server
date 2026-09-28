"""The model registry: which models exist, what they are good at, and which are usable now."""

from __future__ import annotations

import datetime
import logging
import math
import os
import re
import time
from collections.abc import Mapping
from importlib import resources
from pathlib import Path

import httpx
import yaml

from tempo.types import OPEN_LICENCES, TASKS, Access, DataPolicy, ModelInfo, ProviderInfo, Verdict

log = logging.getLogger(__name__)

# Who counts as the owner: the implicit local user and the TEMPO_API_KEY admin.
OWNER_IDS = frozenset({"local", "admin"})

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

    def add(self, model: ModelInfo) -> None:
        if model.provider not in self.providers:
            raise ValueError(f"Model {model.id} uses unknown provider {model.provider!r}")
        self._models.setdefault(model.id, model)

    def _env_list(self, name: str) -> set[str]:
        return {p.strip().lower() for p in self._env.get(name, "").split(",") if p.strip()}

    @property
    def demo_mode(self) -> bool:
        """Demo models are loaded (TEMPO_ENABLE_MOCK, or include_mock when loading)."""
        flag = self._env.get("TEMPO_ENABLE_MOCK", "").strip().lower() in ("1", "true", "yes", "on")
        return flag or "mock" in self.providers

    def is_enabled(self, provider_id: str) -> bool:
        """Providers marked ``enabled: false`` run only when TEMPO_ENABLE_PROVIDERS names them.
        Owner-only providers never run in demo mode."""
        provider = self.providers[provider_id]
        if provider.owner_only and self.demo_mode:
            return False
        return provider.enabled or provider_id in self._env_list("TEMPO_ENABLE_PROVIDERS")

    def is_configured(self, provider_id: str, access: Access | None = None) -> bool:
        provider = self.providers[provider_id]
        if provider.id == "mock":
            return True
        if not self.is_enabled(provider_id):
            return False
        if provider.owner_only and access is not None and access.user_id not in OWNER_IDS:
            return False
        if provider.key_env:
            if access is not None and access.user_keys.get(provider_id):
                return True
            if provider.byok_only:
                return False  # a server-wide key is never used for this provider
            if not self._env.get(provider.key_env, "").strip():
                return False
            return self.openai_base(provider_id) is not None or not provider.openai_base
        if provider.base_env:
            return bool(self._env.get(provider.base_env, "").strip())
        return False

    def openai_base(self, provider_id: str) -> str | None:
        """The provider's OpenAI-compatible base URL, with {NAME} filled from the environment
        (None when a needed variable, such as CLOUDFLARE_ACCOUNT_ID, is missing)."""
        template = self.providers[provider_id].openai_base
        if not template:
            return None
        names = re.findall(r"\{([A-Z0-9_]+)\}", template)
        values = {name: self._env.get(name, "").strip() for name in names}
        if any(not v for v in values.values()):
            return None
        return template.format(**values).rstrip("/")

    def credentials(self, provider_id: str, access: Access | None = None) -> dict[str, str]:
        """Keyword arguments (api_key / api_base) for calling this provider."""
        provider = self.providers[provider_id]
        creds: dict[str, str] = {}
        if provider.key_env and not provider.byok_only:
            if self._env.get(provider.key_env, "").strip():
                creds["api_key"] = self._env[provider.key_env].strip()
        if access is not None and access.user_keys.get(provider_id):
            creds["api_key"] = access.user_keys[provider_id]
        base = self.openai_base(provider_id)
        if base:
            creds["api_base"] = base
        if provider.base_env and self._env.get(provider.base_env, "").strip():
            creds["api_base"] = self._env[provider.base_env].strip().rstrip("/")
        return creds

    def litellm_model(self, model: ModelInfo) -> str:
        """The model string LiteLLM gets: OpenAI-compatible providers use ``openai/<raw id>``."""
        provider = self.providers.get(model.provider)
        if provider is not None and provider.openai_base:
            return "openai/" + model.id.removeprefix(model.provider + "/")
        return model.id

    # --- terms and data policy -----------------------------------------------

    def training_verdict(self, model: ModelInfo) -> Verdict:
        """May this model's outputs be used as training data? A model's own verdict wins; a
        local model decides by its licence (Apache-2.0 or MIT: yes); else the provider's."""
        provider = self.providers.get(model.provider)
        if provider is not None and "export" in provider.blocked_for:
            return "no"  # the owner keeps this provider out of every training export
        if model.training_on_outputs is not None:
            return model.training_on_outputs
        if provider is not None and (provider.local or provider.licence_decides) and model.licence:
            return "yes" if model.licence in OPEN_LICENCES else "unclear"
        return provider.training_on_outputs if provider else "unclear"

    def data_policy(self, model: ModelInfo, access: Access | None = None) -> DataPolicy:
        """What the free tier may do with prompts. A provider the owner opted out of training
        (TEMPO_OPTED_OUT) gets its opt-out policy, for requests on the server's key only."""
        if model.data_policy is not None:
            return model.data_policy
        provider = self.providers.get(model.provider)
        if provider is None:
            return "unknown"
        if provider.local:
            return "ok"
        on_own_key = access is not None and bool(access.user_keys.get(model.provider))
        if (
            provider.opt_out_data_policy is not None
            and provider.id in self._env_list("TEMPO_OPTED_OUT")
            and not on_own_key
        ):
            return provider.opt_out_data_policy
        return provider.data_policy

    def blocked_for(self, provider_id: str, job: str) -> bool:
        provider = self.providers.get(provider_id)
        return provider is not None and job in provider.blocked_for

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
            raw_name = str(tag.get("name") or tag.get("model") or "")
            name = _strip_latest(raw_name)
            if not name or "embed" in name.lower():
                continue
            model_id = f"ollama_chat/{name}"
            installed.add(model_id)
            if model_id not in self._models:
                details = tag.get("details") or {}
                self._models[model_id] = _ollama_model_from_tag(model_id, name, details)
            model = self._models[model_id]
            if model.licence is None:
                model.licence = await _ollama_licence(base, raw_name, transport)

        for model in self._models.values():
            if model.provider == "ollama":
                model.installed = model.id in installed
        return len(installed)


# First lines of licence texts Ollama models ship with -> an SPDX-style id. Anything else is
# recorded as "other", which does not count as open for training.
_LICENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"apache license[,\s]*version 2\.0|\bapache-2\.0\b", "Apache-2.0"),
    (r"^\s*(the )?mit license|permission is hereby granted, free of charge", "MIT"),
    (r"llama 3\.3 community license", "llama3.3"),
    (r"llama 3\.2 community license", "llama3.2"),
    (r"llama 3\.1 community license", "llama3.1"),
    (r"llama (3 )?community license", "llama"),
    (r"gemma terms of use", "gemma"),
    (r"qwen (research )?license agreement", "qwen"),
    (r"creative commons attribution[- ]noncommercial|cc-by-nc", "CC-BY-NC"),
)


def licence_id(text: str | None) -> str | None:
    """A short licence id from a model's licence text (as ``ollama show`` prints it)."""
    if not text or not text.strip():
        return None
    head = text[:3000].lower()
    for pattern, spdx in _LICENCE_PATTERNS:
        if re.search(pattern, head, re.MULTILINE):
            return spdx
    return "other"


async def _ollama_licence(
    base: str, name: str, transport: httpx.AsyncBaseTransport | None
) -> str | None:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            response = await client.post(f"{base}/api/show", json={"model": name})
            response.raise_for_status()
            return licence_id(response.json().get("license"))
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("ollama show %s failed: %s", name, exc)
        return None


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
        source="discovered",
    )


def expired(date: str | None, now: float | None = None) -> bool:
    """True if an expiry date (YYYY-MM-DD) is today or earlier, in UTC."""
    if not isinstance(date, str) or not date:
        return False
    try:
        day = datetime.date.fromisoformat(date[:10])
    except ValueError:
        return False
    moment = time.time() if now is None else now
    return day <= datetime.datetime.fromtimestamp(moment, datetime.UTC).date()
