from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import pytest

from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.providers import Delta
from tempo.registry import Registry
from tempo.types import TASKS, ModelInfo, ProviderInfo

ENV_ALL = {"ALPHA_KEY": "a", "BETA_KEY": "b", "LOCAL_BASE": "http://local"}


def _model(model_id: str, provider: str, **kw: Any) -> ModelInfo:
    skill = kw.pop("skill", kw.get("strength", 0.5))
    return ModelInfo(
        id=model_id,
        provider=provider,
        name=model_id,
        skills={t: skill for t in TASKS},
        **kw,
    )


def make_registry(env: Mapping[str, str] | None = None) -> Registry:
    """A small registry with predictable scores.

    alpha/strong  best quality, slow, very scarce free quota (20/day)
    alpha/small   weak but fast, generous quota
    beta/mid      good quality, fast, generous quota (wins "auto" on easy questions)
    local/tiny    weak, slow, local (no quota)
    """
    providers = {
        "alpha": ProviderInfo(id="alpha", label="Alpha", key_env="ALPHA_KEY"),
        "beta": ProviderInfo(id="beta", label="Beta", key_env="BETA_KEY"),
        "local": ProviderInfo(id="local", label="Local", base_env="LOCAL_BASE", local=True),
    }
    models = [
        _model(
            "alpha/strong",
            "alpha",
            strength=0.9,
            skill=0.9,
            free_rpd=20,
            ttft_ms=2000,
            tokens_per_sec=100,
            context_window=100_000,
            reasoning=True,
        ),
        _model(
            "alpha/small",
            "alpha",
            strength=0.4,
            skill=0.5,
            free_rpd=14400,
            ttft_ms=200,
            tokens_per_sec=1000,
            context_window=8192,
            free_tpm=6000,
        ),
        _model(
            "beta/mid",
            "beta",
            strength=0.7,
            skill=0.75,
            free_rpd=14400,
            ttft_ms=300,
            tokens_per_sec=500,
            context_window=32768,
            vision=True,
        ),
        _model(
            "local/tiny",
            "local",
            strength=0.3,
            skill=0.3,
            ttft_ms=800,
            tokens_per_sec=40,
            context_window=4096,
        ),
    ]
    return Registry(providers, models, env=ENV_ALL if env is None else env)


class ScriptedBackend:
    """Plays back a script per model: a list of deltas and/or an exception to raise."""

    def __init__(self, scripts: dict[str, list[Any]] | None = None) -> None:
        self.scripts = scripts or {}
        self.calls: list[tuple[str, list[dict[str, Any]]]] = []

    async def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Delta]:
        self.calls.append((model.id, [dict(m) for m in messages]))
        script = self.scripts.get(model.id, [("answer", f"Answer from {model.id}.")])
        for item in script:
            if isinstance(item, BaseException):
                raise item
            yield item

    @property
    def called(self) -> list[str]:
        return [model_id for model_id, _ in self.calls]


def make_engine(
    scripts: dict[str, list[Any]] | None = None,
    env: Mapping[str, str] | None = None,
    max_attempts: int = 4,
) -> tuple[Engine, ScriptedBackend]:
    backend = ScriptedBackend(scripts)
    engine = Engine(
        make_registry(env),
        lambda model: backend,
        health=HealthTracker(),
        max_attempts=max_attempts,
    )
    return engine, backend


@pytest.fixture
def registry() -> Registry:
    return make_registry()


def user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]
