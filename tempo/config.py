"""Runtime settings, read from environment variables (and a .env file if present)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    enable_mock: bool = False
    request_timeout: float = 60.0
    max_attempts: int = 4
    models_file: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        if env is None:
            load_dotenv(override=False)
            env = os.environ
        models_file = env.get("TEMPO_MODELS_FILE")
        return cls(
            api_key=env.get("TEMPO_API_KEY") or None,
            enable_mock=env.get("TEMPO_ENABLE_MOCK", "").strip().lower() in _TRUE,
            request_timeout=float(env.get("TEMPO_REQUEST_TIMEOUT") or 60),
            max_attempts=max(1, int(env.get("TEMPO_MAX_ATTEMPTS") or 4)),
            models_file=Path(models_file) if models_file else None,
        )
