"""Core data types shared by the analyzer, router, registry and engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class ModelInfo(BaseModel):
    id: str
    provider: str
    name: str
    family: str = "unknown"
    context_window: int = 8192
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

    def skill(self, task: str) -> float:
        return self.skills.get(task, self.strength)


class QueryProfile(BaseModel):
    """What the analyzer learned about a request."""

    task: Task
    complexity: float
    script: str
    needs: list[str]
    input_tokens: int
    est_output_tokens: int
    has_images: bool = False


class Access(BaseModel):
    """Whose credentials a request uses: the caller's own provider keys first, then the server's.

    Only API keys can be brought by users; base URLs stay server-side so a caller cannot point
    Tempo at internal addresses.
    """

    user_id: str = "local"
    user_keys: dict[str, str] = Field(default_factory=dict, repr=False)

    def key_id(self, provider: str) -> str:
        """Identifies the quota bucket: each user key has its own free-tier limits."""
        return f"user:{self.user_id}" if provider in self.user_keys else "server"
