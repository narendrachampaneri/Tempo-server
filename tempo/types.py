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


class ModelInfo(BaseModel):
    id: str
    provider: str
    name: str
    family: str = "unknown"
    context_window: int = 8192
    strength: float = 0.5
    reasoning: bool = False
    vision: bool = False
    free_rpd: int | None = None
    free_tpm: int | None = None
    ttft_ms: int = 800
    tokens_per_sec: float = 100.0
    skills: dict[str, float] = Field(default_factory=dict)
    # None = unknown; False = the provider reported it is not installed (Ollama discovery).
    installed: bool | None = None

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
