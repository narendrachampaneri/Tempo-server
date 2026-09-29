"""Core data types shared by the analyzer, router, registry and engine."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Task = Literal["chat", "code", "math", "reasoning", "writing", "summarize", "translate", "extract"]
TASKS: tuple[Task, ...] = (
    "chat",
    "code",
    "math",
    "reasoning",
    "writing",
    "summarize",
    "translate",
    "extract",
)

Mode = Literal["auto", "fast", "best", "private"]
MODES: tuple[Mode, ...] = ("auto", "fast", "best", "private")

# What a model does. Only chat-capable types ever receive a chat request.
ModelType = Literal[
    "chat",
    "code",
    "vision",
    "speech-to-text",
    "text-to-speech",
    "safety",
    "embedding",
    "reranker",
    "decision",
]
MODEL_TYPES: tuple[ModelType, ...] = (
    "chat",
    "code",
    "vision",
    "speech-to-text",
    "text-to-speech",
    "safety",
    "embedding",
    "reranker",
    "decision",
)
CHAT_TYPES = frozenset({"chat", "code", "vision"})

Verdict = Literal["yes", "no", "unclear"]
# What a free tier may do with prompts: "ok" (the provider says it neither logs for improvement
# nor trains), "may-log" (kept or logged beyond the request, e.g. to improve products),
# "may-train" (may be used to train models), "unknown" (nothing recorded). Requests marked
# private (privacy "no_logging") never go to "may-log" or "may-train" models.
DataPolicy = Literal["ok", "may-log", "may-train", "unknown"]
FLAGGED_POLICIES = frozenset({"may-log", "may-train"})
# Model licences that allow training on the outputs of a model you run yourself.
OPEN_LICENCES = frozenset({"Apache-2.0", "MIT"})


class ProviderInfo(BaseModel):
    id: str
    label: str
    key_env: str | None = None
    base_env: str | None = None
    local: bool = False
    discover: bool = False
    signup_url: str | None = None
    # Free-tier limits shared by every model of this provider (e.g. OpenRouter's :free pool).
    shared_rpm: int | None = None
    shared_rpd: int | None = None
    # What the provider's x-ratelimit-remaining-requests header counts: "minute" or "day".
    requests_header_window: Literal["minute", "day"] = "minute"
    # When daily free quotas reset: an IANA time zone (Google resets at midnight Pacific).
    day_reset_tz: str = "UTC"
    # "free": a free allowance that renews; "trial": one-off credits that run out.
    free_tier: Literal["free", "trial"] = "free"
    free_tier_note: str | None = None
    # May outputs from this provider's models be used to train other models? "yes", "no" or
    # "unclear", read from the provider's terms (training_terms_url; the deciding sentences are
    # quoted in training_terms_quote). Each model's own licence applies as well. Dataset exports
    # use only "yes" outputs unless --include-unclear, and never "no".
    training_on_outputs: Literal["yes", "no", "unclear"] = "unclear"
    training_terms_url: str | None = None
    training_terms_quote: str | None = None
    training_terms_checked: str | None = None  # when the quote was last checked
    # Off unless TEMPO_ENABLE_PROVIDERS names it (Cerebras: trial credits need a payment method).
    enabled: bool = True
    disabled_note: str | None = None
    # Jobs this provider must never be used for, e.g. ["eval", "collect"].
    blocked_for: list[str] = Field(default_factory=list)
    # Only a user's own key may be used (never a server-wide key).
    byok_only: bool = False
    # Only the owner (the local user or the TEMPO_API_KEY admin) may use it, never other users,
    # and never in demo mode (NVIDIA: its trial terms allow private testing only).
    owner_only: bool = False
    # Outputs' training verdict follows each model's own licence (Apache-2.0/MIT: yes), because
    # the provider's terms put no limit on using outputs (Cloudflare).
    licence_decides: bool = False
    # Data policy once the owner has opted out of training in the provider's console (listed in
    # TEMPO_OPTED_OUT); applies only to requests using the server's key.
    opt_out_data_policy: DataPolicy | None = None
    # OpenAI-compatible base URL. {NAME} is filled from the environment (CLOUDFLARE_ACCOUNT_ID).
    openai_base: str | None = None
    # Public model-list URL (read without a key); when unset, listing needs a key.
    public_models_url: str | None = None
    shared_rpmonth: int | None = None  # requests per calendar month, shared by all models
    free_limit_note: str | None = None  # limits Tempo cannot count itself (e.g. neurons)
    # Where the hand-entered limits above come from, and when they were checked.
    limits_source: str | None = None
    limits_checked: str | None = None
    data_policy: DataPolicy = "unknown"
    data_policy_note: str | None = None
    data_policy_url: str | None = None

    @field_validator("day_reset_tz")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @field_validator("training_on_outputs", mode="before")
    @classmethod
    def _terms_spelling(cls, value: Any) -> Any:
        # YAML reads a bare yes/no as a boolean; Phase 2 used allowed/disallowed/unknown.
        if isinstance(value, bool):
            return "yes" if value else "no"
        return {"allowed": "yes", "disallowed": "no", "unknown": "unclear"}.get(value, value)


class ModelInfo(BaseModel):
    id: str
    provider: str
    name: str
    family: str = "unknown"
    context_window: int = 8192
    context_known: bool = True  # False: the provider did not say (8192 is a cautious guess)
    strength: float = 0.5
    reasoning: bool = False
    vision: bool = False
    free_rpm: int | None = None
    free_rpd: int | None = None
    free_tpm: int | None = None
    free_tpd: int | None = None
    ttft_ms: int = 800
    tokens_per_sec: float = 100.0
    skills: dict[str, float] = Field(default_factory=dict)
    # None = unknown; False = the provider reported it is not installed (Ollama discovery).
    installed: bool | None = None
    # None = not checked; False = the provider's model list no longer includes it.
    listed: bool | None = None
    source: str = "seed"  # "seed" (models.yaml), "sync" (provider list) or "discovered"
    type: ModelType = "chat"
    max_output: int | None = None
    inputs: list[str] = Field(default_factory=lambda: ["text"])
    tools: bool | None = None
    # Native JSON-schema output (response_format). None: unknown, so not sent; False: the
    # provider rejected it once, so it is never sent again.
    structured_outputs: bool | None = None
    parallel_tools: bool | None = None  # several tool calls in one reply
    # Where tools / vision / structured_outputs come from when entered by hand (a keyed sync
    # overwrites them with the provider's own data).
    capabilities_source: str | None = None
    capabilities_checked: str | None = None
    # Preview / experimental / stealth models rank below stable ones and never judge.
    preview: bool = False
    expires: str | None = None  # YYYY-MM-DD; dropped from that day on
    # A specialist (e.g. "finance", "health") answers only questions in its field.
    domain: str | None = None
    # Only used as the very last fallback (e.g. OpenRouter's own openrouter/free router).
    fallback_only: bool = False
    maker_disclosed: bool = True  # False for stealth models whose maker is not named
    licence: str | None = None  # SPDX-style id where known ("Apache-2.0", "MIT", "llama3.2")
    training_on_outputs: Verdict | None = None  # overrides the provider's verdict
    data_policy: DataPolicy | None = None  # overrides the provider's policy
    data_policy_note: str | None = None
    limits_source: str | None = None
    limits_checked: str | None = None
    # Live health, from the provider's endpoint list (OpenRouter).
    endpoints: int | None = None
    endpoint_status: int | None = None  # worst endpoint status; below 0 means degraded
    uptime_30m: float | None = None  # best endpoint's success rate over 30 minutes, 0-100
    health_checked: float | None = None  # unix time of the last check

    def skill(self, task: str) -> float:
        return self.skills.get(task, self.strength)

    @property
    def chat_capable(self) -> bool:
        return self.type in CHAT_TYPES

    @property
    def degraded(self) -> bool:
        return (self.endpoint_status is not None and self.endpoint_status < 0) or (
            self.uptime_30m is not None and self.uptime_30m < 95.0
        )


class QueryProfile(BaseModel):
    """What the analyzer learned about a request."""

    task: Task
    complexity: float
    script: str
    needs: list[str]
    input_tokens: int
    est_output_tokens: int
    has_images: bool = False
    domain: str | None = None  # "finance", "health", ... when the question is in that field


class Access(BaseModel):
    """Whose credentials a request uses: the caller's own provider keys first, then the server's.

    Only API keys can be brought by users; base URLs stay server-side so a caller cannot point
    Tempo at internal addresses.
    """

    user_id: str = "local"
    user_keys: dict[str, str] = Field(default_factory=dict, repr=False)

    def key_id(self, provider: str) -> str:
        """Identifies the quota bucket: each key has its own free-tier limits. Named after a
        one-way fingerprint of the key, so one key used by several callers (the owner, the
        admin, collect) is counted once."""
        key = self.user_keys.get(provider)
        if not key:
            return "server"
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:16]
