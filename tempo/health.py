"""In-memory health tracking: models and providers that just failed are skipped for a while."""

from __future__ import annotations

import time
from collections.abc import Callable

from tempo.types import ModelInfo

# Seconds to skip a model (or a whole provider, for auth errors) after each kind of failure.
COOLDOWNS: dict[str, float] = {
    "rate_limit": 60.0,
    "unavailable": 30.0,
    "timeout": 30.0,
    "empty": 30.0,
    "not_found": 3600.0,
    "auth": 600.0,
    "unknown": 30.0,
    # Request-specific problems: the model is fine for other requests.
    "context": 0.0,
    "bad_request": 0.0,
    "invalid": 0.0,  # the reply didn't fit this request (a bad tool call or JSON)
}


class HealthTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._model_until: dict[str, float] = {}
        self._provider_until: dict[str, float] = {}

    def unavailable_reason(self, model: ModelInfo) -> str | None:
        now = self._clock()
        if self._provider_until.get(model.provider, 0.0) > now:
            return "provider key rejected"
        if self._model_until.get(model.id, 0.0) > now:
            return "cooling down"
        return None

    def record_failure(self, model: ModelInfo, kind: str, retry_after: float | None = None) -> None:
        seconds = COOLDOWNS.get(kind, COOLDOWNS["unknown"])
        if kind == "rate_limit" and retry_after:
            seconds = max(1.0, min(retry_after, 3600.0))
        if seconds <= 0:
            return
        until = self._clock() + seconds
        if kind == "auth":
            self._provider_until[model.provider] = until
        else:
            self._model_until[model.id] = until

    def record_success(self, model: ModelInfo) -> None:
        self._model_until.pop(model.id, None)
