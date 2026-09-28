"""Runtime settings, read from environment variables (and a .env file if present)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

# Decisions Laya can make. Each one runs in shadow mode (Laya predicts, rules decide, both are
# logged) until TEMPO_LAYA_TAKEOVER hands it to Laya.
LAYA_DECISIONS = (
    "task_type",
    "difficulty",
    "strategy",
    "stage_budget",
    "quality",
    "should_stop",
    "next_model",
)
LAYA_MODES = ("shadow", "laya", "auto")

# Hard ceiling on stages a single request may ask for.
MAX_STAGES_LIMIT = 50


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def parse_takeover(spec: str) -> dict[str, str]:
    """``"should_stop, task_type=auto"`` -> {"should_stop": "laya", "task_type": "auto"}."""
    modes: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, mode = part.partition("=")
        name, mode = name.strip(), (mode.strip() or "laya")
        if name == "all":
            modes.update({d: mode for d in LAYA_DECISIONS})
            continue
        if name not in LAYA_DECISIONS:
            raise ValueError(f"Unknown Laya decision {name!r}; expected one of {LAYA_DECISIONS}")
        if mode not in LAYA_MODES:
            raise ValueError(f"Unknown Laya mode {mode!r} for {name}; expected {LAYA_MODES}")
        modes[name] = mode
    return modes


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    enable_mock: bool = False
    request_timeout: float = 60.0
    max_attempts: int = 4
    models_file: Path | None = None

    # Storage (logs for tuning, quota counters, users, measured scores).
    data_dir: Path | None = None  # None: in-memory database, nothing written to disk
    log_questions: bool = True

    # Staged engine budgets (per question; requests can lower or raise them).
    max_stages: int = 5
    time_budget_s: float = 60.0
    quota_budget: int = 12  # provider requests per question; local models do not count
    max_parallel: int = 3  # models called in parallel within one stage
    judge: bool = True  # use an LLM judge in check stages

    # Laya (optional dependency).
    laya: str = "auto"  # "auto": use it when installed; "off": never load it
    laya_timeout_ms: float = 200.0
    laya_device: str | None = None
    # A fine-tuned Laya checkpoint (local folder or Hub repo) that answers every decision.
    laya_model: str | None = None
    laya_takeover: dict[str, str] = field(default_factory=dict)
    laya_min_confidence: float = 0.6  # below this, a taken-over decision falls back to rules

    # Embedding classifier and semantic cache (optional fastembed dependency).
    embeddings: str = "auto"  # "auto" | "off"
    embedding_model: str | None = None  # fastembed model name; default BAAI/bge-small-en-v1.5
    cache: bool = True
    cache_ttl_s: float = 24 * 3600

    # Bring-your-own-key vault; if unset, a key file is created in data_dir.
    secret_key: str | None = None

    # Provider model-list sync and listing-based health checks; 0 disables.
    sync_interval_s: float = 6 * 3600

    @property
    def db_path(self) -> Path | None:
        return self.data_dir / "tempo.db" if self.data_dir else None

    def laya_mode(self, decision: str) -> str:
        return self.laya_takeover.get(decision, "shadow")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        if env is None:
            load_dotenv(override=False)
            env = os.environ
        models_file = env.get("TEMPO_MODELS_FILE")
        data_dir = env.get("TEMPO_DATA_DIR", "").strip()
        if data_dir.lower() == "memory":
            resolved_dir = None
        else:
            resolved_dir = Path(data_dir).expanduser() if data_dir else Path.home() / ".tempo"
        return cls(
            api_key=env.get("TEMPO_API_KEY") or None,
            enable_mock=_flag(env, "TEMPO_ENABLE_MOCK", False),
            request_timeout=float(env.get("TEMPO_REQUEST_TIMEOUT") or 60),
            max_attempts=max(1, int(env.get("TEMPO_MAX_ATTEMPTS") or 4)),
            models_file=Path(models_file) if models_file else None,
            data_dir=resolved_dir,
            log_questions=_flag(env, "TEMPO_LOG", True),
            max_stages=min(MAX_STAGES_LIMIT, max(1, int(env.get("TEMPO_MAX_STAGES") or 5))),
            time_budget_s=float(env.get("TEMPO_TIME_BUDGET") or 60),
            quota_budget=max(1, int(env.get("TEMPO_QUOTA_BUDGET") or 12)),
            max_parallel=max(1, int(env.get("TEMPO_MAX_PARALLEL") or 3)),
            judge=_flag(env, "TEMPO_JUDGE", True),
            laya=(env.get("TEMPO_LAYA") or "auto").strip().lower(),
            laya_timeout_ms=float(env.get("TEMPO_LAYA_TIMEOUT_MS") or 200),
            laya_device=env.get("TEMPO_LAYA_DEVICE") or None,
            laya_model=(env.get("TEMPO_LAYA_MODEL") or "").strip() or None,
            laya_takeover=parse_takeover(env.get("TEMPO_LAYA_TAKEOVER", "")),
            laya_min_confidence=float(env.get("TEMPO_LAYA_MIN_CONFIDENCE") or 0.6),
            embeddings=(env.get("TEMPO_EMBEDDINGS") or "auto").strip().lower(),
            embedding_model=env.get("TEMPO_EMBEDDING_MODEL") or None,
            cache=_flag(env, "TEMPO_CACHE", True),
            cache_ttl_s=float(env.get("TEMPO_CACHE_TTL") or 24 * 3600),
            secret_key=env.get("TEMPO_SECRET_KEY") or None,
            sync_interval_s=float(env.get("TEMPO_SYNC_INTERVAL") or 6 * 3600),
        )
