from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

import pytest

from tempo.config import Settings
from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.providers import Delta
from tempo.registry import Registry
from tempo.types import TASKS, ModelInfo, ProviderInfo


@pytest.fixture(autouse=True)
def _private_home(tmp_path_factory, monkeypatch):
    """Every test gets its own empty home and app-data folders, so nothing reads or moves the
    real ~/.tempo or the user's data folder, on any system."""
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    return home


ENV_ALL = {"ALPHA_KEY": "a", "BETA_KEY": "b", "LOCAL_BASE": "http://local"}


def _model(model_id: str, provider: str, **kw: Any) -> ModelInfo:
    skill = kw.pop("skill", kw.get("strength", 0.5))
    return ModelInfo(
        id=model_id,
        provider=provider,
        name=model_id,
        family=provider,
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


def sleep(seconds: float) -> tuple[str, float]:
    """Script item: wait before the next item (to simulate a slow model)."""
    return ("sleep", seconds)


def finish(reason: str) -> tuple[str, str]:
    """Script item: the provider's finish_reason for this reply (e.g. "length")."""
    return ("finish", reason)


def judge_reply(scores: Sequence[float] | Callable[[list[str]], list[float]]):
    """Script for a judge: grades every candidate in the prompt."""

    def reply(messages: list[dict[str, Any]]) -> list[Any]:
        prompt = str(messages[-1]["content"])
        candidates = re.findall(r'<candidate id="\d+">\n(.*?)\n</candidate>', prompt, re.DOTALL)
        values = scores(candidates) if callable(scores) else list(scores)
        grades = [
            {"id": i, "score": values[min(i - 1, len(values) - 1)], "issues": ["needs work"]}
            for i in range(1, len(candidates) + 1)
        ]
        return [("answer", json.dumps({"grades": grades}))]

    return reply


def _asks_function(messages: list[dict[str, Any]]) -> bool:
    return any(
        m.get("role") == "user" and "python function" in str(m.get("content")).lower()
        for m in messages
    )


class ScriptedBackend:
    """Plays back a script per model and purpose.

    Scripts are looked up as "model:purpose", then "model" (drafts only), then "*:purpose".
    A script is a list of deltas, exceptions to raise and sleep(...) items, or a callable that
    takes the messages and returns such a list. Judges grade every candidate 8 by default.
    """

    def __init__(self, scripts: dict[str, Any] | None = None, judge_score: float = 8) -> None:
        self.scripts = scripts or {}
        self.judge_score = judge_score
        self.calls: list[tuple[str, list[dict[str, Any]], str | None]] = []
        self.accesses: list[Any] = []
        self.extras: list[dict[str, Any]] = []  # tools / tool_choice sent with each call
        self.active = 0
        self.max_active = 0

    def _script(self, model_id: str, purpose: str | None, messages: list[dict[str, Any]]):
        purpose = purpose or "draft"
        for key in (
            f"{model_id}:{purpose}",
            model_id if purpose == "draft" else None,
            f"*:{purpose}",
        ):
            if key and key in self.scripts:
                script = self.scripts[key]
                return script(messages) if callable(script) else script
        if purpose == "judge":
            return judge_reply([self.judge_score])(messages)
        if purpose == "split":
            return [("answer", json.dumps({"parts": ["first part", "second part"]}))]
        # Asked to write a function: answer with code, as the checker expects.
        code = (
            "\n\n```python\ndef f(s):\n    return s[::-1]\n```" if _asks_function(messages) else ""
        )
        if purpose == "draft":
            return [("answer", f"Answer from {model_id}.{code}")]
        return [("answer", f"{purpose.capitalize()} from {model_id}.{code}")]

    async def stream(
        self,
        model: ModelInfo,
        messages: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[Delta]:
        purpose = kwargs.get("purpose")
        copied = [dict(m) for m in messages]
        self.calls.append((model.id, copied, purpose))
        self.accesses.append(kwargs.get("access"))
        self.extras.append(
            {k: kwargs.get(k) for k in ("tools", "tool_choice", "response_format") if kwargs.get(k)}
        )
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            for item in self._script(model.id, purpose, copied):
                if isinstance(item, BaseException):
                    raise item
                if item[0] == "sleep":
                    await asyncio.sleep(item[1])
                    continue
                if item[0] == "finish":
                    if kwargs.get("meta") is not None:
                        kwargs["meta"]["finish_reason"] = item[1]
                    continue
                yield item
        finally:
            self.active -= 1

    @property
    def called(self) -> list[str]:
        return [model_id for model_id, _, _ in self.calls]

    def called_for(self, purpose: str) -> list[str]:
        return [m for m, _, p in self.calls if (p or "draft") == purpose]


def make_engine(
    scripts: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    max_attempts: int = 4,
    judge_score: float = 8,
    **settings: Any,
) -> tuple[Engine, ScriptedBackend]:
    backend = ScriptedBackend(scripts, judge_score=judge_score)
    settings.setdefault("local_first", "off")  # tests of local first turn it on themselves
    settings.setdefault("sandbox", "off")  # sandbox tests turn it on (tests/test_sandbox*.py)
    engine = Engine(
        make_registry(env),
        lambda model: backend,
        settings=Settings(max_attempts=max_attempts, **settings),
        health=HealthTracker(),
    )
    return engine, backend


@pytest.fixture
def registry() -> Registry:
    return make_registry()


def user(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]
