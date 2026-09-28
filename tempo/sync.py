"""Registry auto-sync and health checks, using each provider's own model list.

Listing models costs no generation quota, so it doubles as a health check: a 401/403 means the
key is bad (the whole provider cools down), a timeout or 5xx is reported as degraded. Models a
provider no longer lists are marked ``listed=False`` and skipped by the router; new models are
added with their type (chat, code, vision, speech-to-text, ...) and priors guessed from their
name and size until `tempo eval` measures them.

Public lists are read without a key (OpenRouter, NVIDIA, OpenCode Zen); the others only when a
key is set. OpenRouter's free models also get a health check from their endpoint list, at most
every 15 minutes: no endpoints means dropped, status below 0 or under 95% success means ranked
down.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from tempo.health import HealthTracker
from tempo.providers import KEY_REJECTED
from tempo.registry import Registry, expired
from tempo.types import TASKS, ModelInfo, ModelType

log = logging.getLogger(__name__)

# Where each provider lists its models ({NAME} comes from the environment). Providers with an
# ``openai_base`` in models.yaml and no entry here list at ``<openai_base>/models``.
MODEL_LISTS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "cerebras": "https://api.cerebras.ai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    "cloudflare": (
        "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/models/search"
    ),
    "cohere": "https://api.cohere.com/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "opencode": "https://opencode.ai/zen/v1/models",
}
# Lists anyone may read without a key (no inference happens, so no free pool is used).
PUBLIC_LISTS = frozenset({"openrouter", "nvidia", "opencode"})
LIST_PARAMS = {
    "gemini": {"pageSize": "1000"},
    "cloudflare": {"per_page": "1000"},
    "cohere": {"page_size": "1000"},
}
HEALTH_EVERY_S = 15 * 60  # OpenRouter endpoint checks, at most this often per model
HEALTH_PARALLEL = 4

# Never registered: generators of images, video or music (no type Tempo can use).
NOT_REGISTERED = re.compile(
    r"imagen|veo|lyria|music|flux|stable-diffusion|sdxl|dall-e|video-detector|"
    r"text-to-image|image-generation|\bcompound\b|\baqa\b",
    re.IGNORECASE,
)
# Name patterns per type, checked in this order.
TYPE_PATTERNS: tuple[tuple[ModelType, re.Pattern[str]], ...] = (
    ("speech-to-text", re.compile(r"whisper|transcri|\basr\b|speech-to-text|parakeet", re.I)),
    ("text-to-speech", re.compile(r"\btts\b|-tts|orpheus|playai|text-to-speech|melotts", re.I)),
    (
        "safety",
        re.compile(
            r"guard|safeguard|safety|moderation|content-safety|topic-control|shieldgemma", re.I
        ),
    ),
    ("reranker", re.compile(r"rerank", re.I)),
    ("embedding", re.compile(r"embed|retriever|nvclip|\bbge-|\be5-", re.I)),
    ("decision", re.compile(r"reward|jev-|calibration", re.I)),
    (
        "code",
        re.compile(r"coder|codestral|starcoder|codegemma|codellama|-code\b|code-|devstral", re.I),
    ),
    (
        "vision",
        re.compile(
            r"vision|-vl\b|-vlm|\bvila\b|neva|kosmos|fuyu|deplot|nemotron-parse|\bocr\b|"
            r"pixtral|paligemma|llava",
            re.I,
        ),
    ),
)
# Specialists: they answer only questions in their own field.
DOMAIN_PATTERNS = {
    "finance": re.compile(r"-fin\b|-fin-|finance|fingpt|palmyra-fin", re.I),
    "health": re.compile(r"-med\b|-med-|medgemma|meditron|sante|health|clinical", re.I),
}
PREVIEW = re.compile(r"preview|\balpha\b|\bbeta\b|experimental|-exp\b|^stealth/", re.I)
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


def classify_type(model_id: str, inputs: list[str] | None = None) -> ModelType:
    """A model's type from its name (and inputs when the provider lists them)."""
    for kind, pattern in TYPE_PATTERNS:
        if pattern.search(model_id):
            return kind
    if inputs and "text" not in inputs and ("audio" in inputs):
        return "speech-to-text"
    return "chat"


def classify_domain(model_id: str) -> str | None:
    for domain, pattern in DOMAIN_PATTERNS.items():
        if pattern.search(model_id):
            return domain
    return None


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


def guess_model(
    model_id: str,
    provider: str,
    name: str | None,
    context: int | None,
    hint: str | None = None,
    reasoning: bool | None = None,
    **extra: Any,
) -> ModelInfo:
    """Priors for a model we have never measured, from its size and family, plus its type,
    field and preview status from its name. ``extra`` sets any other ModelInfo field.

    ``hint`` is another name for the same weights (OpenRouter's ``hugging_face_id``), used when
    the id itself gives no size or family. ``reasoning`` is the provider's own answer, when it
    gives one; otherwise it is guessed from the model's name (not its organisation's).
    """
    low = model_id.lower()
    base = low.rsplit("/", 1)[-1]
    alt = (hint or "").lower()
    family = next((f for f in FAMILIES if f in low), None)
    family = family or next((f for f in FAMILIES if f in alt), None)
    # Unknown family: the organisation is the next best guess, so the judge's "different
    # family" rule doesn't treat every unfamiliar model as one family.
    family = family or (low.split("/", 1)[0] if "/" in low else "unknown")
    size_pattern = r"(\d+(?:\.\d+)?)b\b"
    sizes = [float(x) for x in re.findall(size_pattern, low)]
    sizes = sizes or [float(x) for x in re.findall(size_pattern, alt)]
    params = max(sizes) if sizes else 30.0
    active = re.search(r"a(\d+(?:\.\d+)?)b\b", low) or re.search(r"a(\d+(?:\.\d+)?)b\b", alt)
    strength = min(0.85, max(0.2, 0.15 + 0.11 * math.log2(max(params, 1.0))))
    speed = float(active.group(1)) if active else params
    general = {"chat", "writing", "summarize"}
    skills = {t: round(min(0.9, strength + (0.05 if t in general else 0.0)), 2) for t in TASKS}
    kind = extra.pop("type", None) or classify_type(model_id, extra.get("inputs"))
    if kind == "code":
        skills["code"] = min(0.9, strength + 0.1)
    values: dict[str, Any] = {
        "id": f"{provider}/{model_id}",
        "provider": provider,
        "name": name or model_id,
        "family": family,
        "context_window": int(context or 8192),
        "strength": round(strength, 2),
        "reasoning": (
            reasoning
            if reasoning is not None
            else any(k in base for k in ("reason", "think", "r1", "gpt-oss", "qwq"))
        ),
        "ttft_ms": 800,
        "tokens_per_sec": max(30.0, 3000.0 / max(speed, 1.0)),
        "skills": skills,
        "source": "sync",
        "listed": True,
        "type": kind,
        "domain": classify_domain(model_id),
        "preview": bool(PREVIEW.search(model_id) or PREVIEW.search(name or "")),
    }
    values.update({k: v for k, v in extra.items() if v is not None})
    if "image" in (values.get("inputs") or []):
        values["vision"] = True
    return ModelInfo(**values)


# Hand-entered facts the providers' lists don't carry, each with its source and date.
# Cloudflare: "Some models require a paid billing method" (developers.cloudflare.com/workers-ai/
# platform/pricing/, checked 2026-09-28). Tempo never uses them (free only).
CLOUDFLARE_PAID_ONLY = frozenset(
    {
        "@cf/moonshotai/kimi-k2.6",
        "@cf/moonshotai/kimi-k2.7-code",
        "@cf/zai-org/glm-5.2",
        "@cf/zai-org/glm-5.3",
        "@cf/zai-org/glm-5.3-flash",
        "@cf/deepseek-ai/deepseek-v4-flash-0731",
        "@cf/deepseek-ai/deepseek-v4-pro-0813",
    }
)
CLOUDFLARE_TASKS: dict[str, ModelType] = {
    "text generation": "chat",
    "text embeddings": "embedding",
    "automatic speech recognition": "speech-to-text",
    "text-to-speech": "text-to-speech",
    "text classification": "decision",
    "image-to-text": "vision",
}
# OpenCode Zen (opencode.ai/docs/zen, "Pricing" and "Privacy", checked 2026-09-28). Free models
# are the ids ending in -free plus these; stealth models are previews and, by the owner's rule,
# flagged as possibly logging prompts.
ZEN_FREE = frozenset({"big-pickle"})
ZEN_STEALTH = frozenset({"big-pickle", "space-bunny-free"})
ZEN_DATA_POLICY: dict[str, str] = {
    "big-pickle": "may-train",  # "collected data may be used to improve the model"
    "mimo-v2.6-flash-free": "may-train",
    "mimo-v2.5-free": "may-train",
    "ling-3.0-flash-fin-free": "may-train",
    "nemotron-3-ultra-free": "may-train",  # NVIDIA free endpoints: logged, used to improve
    "nemotron-3.5-lightning-free": "may-train",
    "space-bunny-free": "may-log",  # stealth (owner's rule), though Zen says zero retention
    "longcat-2.5-preview-free": "ok",  # "zero-retention policy and does not use your data"
}
# Free Zen models served only on /responses or /systemone, which LiteLLM's OpenAI-compatible
# chat route can't call (same page, "Endpoints"). Jev is a decision model (typed questions).
ZEN_NOT_CHAT_COMPLETIONS = re.compile(r"contributor-free$", re.I)


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
        self._live: dict[str, dict[str, Any]] = {}  # model id -> fields the provider reported

    def list_url(self, provider: str) -> str | None:
        template = MODEL_LISTS.get(provider)
        if template is None:
            base = self.registry.openai_base(provider)
            return f"{base}/models" if base else None
        names = re.findall(r"\{([A-Z0-9_]+)\}", template)
        env = self.registry._env
        values = {n: env.get(n, "").strip() for n in names}
        if any(not v for v in values.values()):
            return None
        return template.format(**values)

    def wanted(self, provider: str) -> bool:
        if provider not in self.registry.providers or not self.registry.is_enabled(provider):
            return False
        if provider in PUBLIC_LISTS:
            return True
        return self.registry.is_configured(provider)

    async def run(self) -> dict[str, ProviderStatus]:
        async with httpx.AsyncClient(transport=self.transport, timeout=15.0) as client:
            for provider in MODEL_LISTS.keys() | {
                p for p, info in self.registry.providers.items() if info.openai_base
            }:
                if not self.wanted(provider):
                    continue
                self.status[provider] = await self._sync(client, provider)
            if "openrouter" in self.status and self.status["openrouter"].ok:
                await self.check_openrouter_health(client)
                await self._openrouter_key_limit(client)
        if "ollama" in self.registry.providers and self.registry.is_configured("ollama"):
            found = await self.registry.discover_ollama(transport=self.transport)
            self.status["ollama"] = ProviderStatus("ollama", found > 0, self._clock(), found)
        return self.status

    async def _sync(self, client: httpx.AsyncClient, provider: str) -> ProviderStatus:
        url = self.list_url(provider)
        if url is None:
            return ProviderStatus(provider, False, self._clock(), error="not configured")
        creds = self.registry.credentials(provider)
        headers = auth_headers(provider, creds.get("api_key"))
        try:
            response = await client.get(url, headers=headers, params=LIST_PARAMS.get(provider))
        except httpx.HTTPError as exc:
            return ProviderStatus(provider, False, self._clock(), error=f"unreachable: {exc}")
        if key_rejected(response):
            self._reject_key(provider)
            return ProviderStatus(provider, False, self._clock(), error="API key rejected")
        if response.status_code >= 400:
            return ProviderStatus(
                provider, False, self._clock(), error=f"HTTP {response.status_code}"
            )
        try:
            listed = self._parse(provider, response.json())
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            return ProviderStatus(provider, False, self._clock(), error=f"bad model list: {exc}")
        added, removed = self._apply(provider, listed)
        return ProviderStatus(provider, True, self._clock(), len(listed), added, removed)

    # --- parsing each provider's list --------------------------------------------------------

    def _add(self, out: dict[str, ModelInfo], raw: str, provider: str, **fields: Any) -> None:
        if NOT_REGISTERED.search(raw):
            return
        live = {k: v for k, v in fields.items() if v is not None}
        name = live.pop("name", None)
        context = live.pop("context_window", None)
        hint = live.pop("hint", None)
        reasoning = live.pop("reasoning", None)
        model = guess_model(raw, provider, name, context, hint, reasoning, **live)
        out[model.id] = model
        report = {k: v for k, v in live.items() if k in LIVE_FIELDS}
        if context:
            report["context_window"] = int(context)
        if reasoning is not None:
            report["reasoning"] = reasoning
        self._live[model.id] = report

    def _parse(self, provider: str, data: Any) -> dict[str, ModelInfo]:
        parser = getattr(self, f"_parse_{provider}", self._parse_openai)
        out: dict[str, ModelInfo] = {}
        parser(provider, data, out)
        return out

    def _parse_openai(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("data", []):
            raw = str(item["id"])
            if item.get("active") is False:
                continue
            context = (
                item.get("context_length")
                or item.get("context_window")
                or item.get("max_model_len")
            )
            self._add(out, raw, provider, name=item.get("name"), context_window=context)

    def _parse_gemini(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("models", []):
            methods = item.get("supportedGenerationMethods", [])
            raw = str(item["name"]).removeprefix("models/")
            if "generateContent" in methods:
                kind = None
            elif "embedContent" in methods:
                kind = "embedding"
            else:
                continue
            self._add(
                out,
                raw,
                provider,
                name=item.get("displayName"),
                context_window=item.get("inputTokenLimit"),
                max_output=item.get("outputTokenLimit"),
                type=kind,
            )

    def _parse_openrouter(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("data", []):
            raw = str(item["id"])
            pricing = item.get("pricing") or {}
            if str(pricing.get("prompt")) != "0" or str(pricing.get("completion")) != "0":
                continue  # free models only, with or without the :free suffix
            if expired(item.get("expiration_date"), self._clock()):
                continue
            arch = item.get("architecture") or {}
            if "text" not in (arch.get("output_modalities") or ["text"]):
                continue
            params = item.get("supported_parameters")
            top = item.get("top_provider") or {}
            stealth = raw.lower().startswith("stealth/")
            self._add(
                out,
                raw,
                provider,
                name=item.get("name"),
                context_window=item.get("context_length"),
                hint=item.get("hugging_face_id"),
                reasoning=("reasoning" in params) if isinstance(params, list) else None,
                max_output=top.get("max_completion_tokens"),
                inputs=arch.get("input_modalities"),
                tools=("tools" in params) if isinstance(params, list) else None,
                expires=item.get("expiration_date"),
                # OpenRouter's own router: only ever the very last fallback.
                fallback_only=True if raw == "openrouter/free" else None,
                family="openrouter-router" if raw == "openrouter/free" else None,
                preview=True if stealth else None,
                data_policy="may-log" if stealth else None,
            )

    def _parse_cloudflare(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("result") or []:
            raw = str(item["name"])
            if raw in CLOUDFLARE_PAID_ONLY:
                continue
            task = str((item.get("task") or {}).get("name", "")).lower()
            kind = CLOUDFLARE_TASKS.get(task)
            if kind is None:
                continue  # images, translation, summarization-only models
            props = {
                str(p.get("property_id")): p.get("value") for p in item.get("properties") or []
            }
            by_name = classify_type(raw)
            self._add(
                out,
                raw,
                provider,
                context_window=_int(props.get("context_window") or props.get("max_input_tokens")),
                max_output=_int(props.get("max_total_tokens") or props.get("max_output_tokens")),
                tools=str(props.get("function_calling", "")).lower() == "true" or None,
                preview=str(props.get("beta", "")).lower() == "true" or None,
                expires=props.get("planned_deprecation_date"),
                type=by_name if kind == "chat" and by_name != "chat" else kind,
            )

    def _parse_cohere(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("models") or []:
            raw = str(item["name"])
            if item.get("is_deprecated"):
                continue
            endpoints = set(item.get("endpoints") or [])
            if "chat" in endpoints:
                kind = None  # by name: vision, code or chat
            elif "rerank" in endpoints:
                kind = "reranker"
            elif "embed" in endpoints:
                kind = "embedding"
            elif "classify" in endpoints:
                kind = "decision"
            else:
                continue
            features = set(item.get("features") or [])
            self._add(
                out,
                raw,
                provider,
                context_window=item.get("context_length"),
                tools=bool(features & {"tools", "tool_use"}) or None,
                type=kind,
            )

    def _parse_mistral(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("data", []):
            raw = str(item["id"])
            caps = item.get("capabilities") or {}
            by_name = classify_type(raw)
            if caps.get("completion_chat"):
                kind = by_name if by_name in ("code", "vision") else "chat"
            elif by_name in ("embedding", "safety", "speech-to-text"):
                kind = by_name
            elif caps.get("classification"):
                kind = "safety" if "moderation" in raw else "decision"
            else:
                continue
            deprecation = item.get("deprecation")
            self._add(
                out,
                raw,
                provider,
                name=item.get("name"),
                context_window=item.get("max_context_length"),
                tools=caps.get("function_calling"),
                inputs=["text", "image"] if caps.get("vision") else None,
                expires=str(deprecation)[:10] if deprecation else None,
                type=kind,
            )

    def _parse_opencode(self, provider: str, data: Any, out: dict[str, ModelInfo]) -> None:
        for item in data.get("data", []):
            raw = str(item["id"])
            if not (raw.endswith("-free") or raw in ZEN_FREE):
                continue  # free models only; everything else is paid
            if ZEN_NOT_CHAT_COMPLETIONS.search(raw):
                continue
            self._add(
                out,
                raw,
                provider,
                preview=True if raw in ZEN_STEALTH else None,
                data_policy=ZEN_DATA_POLICY.get(raw),
            )

    # --- applying a list ---------------------------------------------------------------------

    def _apply(self, provider: str, listed: dict[str, ModelInfo]) -> tuple[list[str], list[str]]:
        added, removed = [], []
        for model in self.registry.all():
            if model.provider != provider:
                continue
            was = model.listed
            model.listed = model.id in listed
            if was is not False and not model.listed:
                removed.append(model.id)
            elif model.listed:
                # Live facts replace the seed's hand-entered ones.
                for name, value in self._live.get(model.id, {}).items():
                    setattr(model, name, value)
        for model_id, model in listed.items():
            if self.registry.get(model_id) is None:
                self.registry.add(model)
                added.append(model_id)
        if added or removed:
            log.info("%s: %d new models, %d no longer listed", provider, len(added), len(removed))
        return added, removed

    # --- OpenRouter: endpoint health and the key's live free limit ---------------------------

    async def check_openrouter_health(
        self, client: httpx.AsyncClient | None = None, force: bool = False
    ) -> int:
        """Read each listed free model's endpoints (at most every 15 minutes per model).
        Returns how many models were checked."""
        now = self._clock()
        due = [
            m
            for m in self.registry.all()
            if m.provider == "openrouter"
            and m.listed is not False
            and not m.fallback_only
            and (force or m.health_checked is None or now - m.health_checked >= HEALTH_EVERY_S)
        ]
        if not due:
            return 0
        own = client is None
        client = client or httpx.AsyncClient(transport=self.transport, timeout=15.0)
        gate = asyncio.Semaphore(HEALTH_PARALLEL)

        async def one(model: ModelInfo) -> None:
            async with gate:
                await self._endpoints(client, model)

        try:
            await asyncio.gather(*(one(m) for m in due))
        finally:
            if own:
                await client.aclose()
        return len(due)

    async def _endpoints(self, client: httpx.AsyncClient, model: ModelInfo) -> None:
        raw = model.id.removeprefix("openrouter/")
        url = f"{MODEL_LISTS['openrouter'].removesuffix('/models')}/models/{raw}/endpoints"
        try:
            response = await client.get(url)
            response.raise_for_status()
            endpoints = (response.json().get("data") or {}).get("endpoints") or []
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            log.debug("endpoints of %s: %s", model.id, exc)
            return  # unknown: leave the last known health
        model.health_checked = self._clock()
        model.endpoints = len(endpoints)
        statuses = [e.get("status") for e in endpoints if isinstance(e.get("status"), int)]
        uptimes = [
            float(e["uptime_last_30m"])
            for e in endpoints
            if isinstance(e.get("uptime_last_30m"), int | float)
        ]
        model.endpoint_status = max(statuses) if statuses else None  # the best endpoint
        model.uptime_30m = round(max(uptimes), 2) if uptimes else None
        caps = [e["max_completion_tokens"] for e in endpoints if e.get("max_completion_tokens")]
        if caps:
            model.max_output = max(caps)

    async def _openrouter_key_limit(self, client: httpx.AsyncClient) -> None:
        """With a key, OpenRouter reports the free-model daily limit that applies to it."""
        api_key = self.registry.credentials("openrouter").get("api_key")
        if not api_key:
            return
        url = f"{MODEL_LISTS['openrouter'].removesuffix('/models')}/key"
        try:
            response = await client.get(url, headers=auth_headers("openrouter", api_key))
            response.raise_for_status()
            daily = ((response.json().get("data") or {}).get("free_model_daily_requests")) or {}
        except (httpx.HTTPError, ValueError, AttributeError):
            return
        limit = daily.get("limit")
        if isinstance(limit, int) and limit > 0:
            provider = self.registry.providers["openrouter"]
            provider.shared_rpd = limit
            provider.limits_source = "live: GET https://openrouter.ai/api/v1/key"
            provider.limits_checked = time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def _reject_key(self, provider: str) -> None:
        if self.health is None:
            return
        for model in self.registry.all():
            if model.provider == provider:
                self.health.record_failure(model, "auth")
                break


# Fields a provider's live list overrides on seeded models.
LIVE_FIELDS = frozenset(
    {"context_window", "max_output", "inputs", "tools", "expires", "reasoning", "vision"}
)


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def key_rejected(response: httpx.Response) -> bool:
    """401/403, or a 400 whose message says the key is bad (Google answers that way)."""
    if response.status_code in (401, 403):
        return True
    return response.status_code == 400 and bool(KEY_REJECTED.search(response.text[:500]))


def auth_headers(provider: str, api_key: str | None) -> dict[str, str]:
    """The key goes in a header, never in the URL (where logs and proxies would keep it):
    Google AI Studio reads x-goog-api-key, the others a Bearer token."""
    if not api_key:
        return {}
    if provider == "gemini":
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


async def verify_key(
    registry: Registry,
    provider: str,
    api_key: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool | None:
    """Check a provider key by listing models with it: True ok, False rejected, None unknown."""
    base = registry.providers[provider].base_env and registry.credentials(provider).get("api_base")
    url = f"{base}/models" if base else RegistrySync(registry).list_url(provider)
    if url is None:
        return None
    headers = auth_headers(provider, api_key)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            response = await client.get(url, headers=headers, params=LIST_PARAMS.get(provider))
    except httpx.HTTPError:
        return None
    if key_rejected(response):
        return False
    return True if response.status_code < 400 else None
