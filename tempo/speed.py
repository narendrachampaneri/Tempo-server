"""Measured speed per model: a rolling record of time to first token and tokens a second.

Every successful call updates it at once (private calls too: only timings are kept, never
text), and the server loads the recent calls from the log at start-up. A model's figures are
the median of its last ``WINDOW`` calls, blended with the registry's prior until a few calls
are in. The router ranks with them, and the pipeline uses them to plan stages that fit the
time budget and to skip a model that couldn't finish in the time left.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tempo.types import ModelInfo

if TYPE_CHECKING:
    from tempo.registry import Registry
    from tempo.store import Store

WINDOW = 20  # calls kept per model
PRIOR_SAMPLES = 2.0  # the registry's prior counts like two measured calls
LOAD_DAYS = 14  # how far back the log is read at start-up
# Extra hidden "thinking" tokens a reasoning model spends before answering.
REASONING_OVERHEAD_TOKENS = 300
MIN_TPS_TOKENS = 20  # replies shorter than this say little about tokens a second
MIN_GEN_S = 0.2


@dataclass(frozen=True)
class Sample:
    ttft_s: float
    tps: float | None
    total_s: float


@dataclass(frozen=True)
class Speed:
    ttft_s: float
    tps: float
    n: int  # measured calls behind these figures (0: the prior only)

    def as_dict(self) -> dict[str, Any]:
        return {"ttft_s": round(self.ttft_s, 2), "tps": round(self.tps, 1), "n": self.n}


def estimate_seconds(model: ModelInfo, out_tokens: int) -> float:
    """Seconds this model needs for an answer of ``out_tokens`` tokens: time to the first
    token, the tokens at its speed, and one more first token for each extra piece when the
    answer is longer than its output limit (the pipeline continues such answers). Uses the
    model's figures, which ``SpeedBook`` keeps up to date from measured calls."""
    tokens = out_tokens + (REASONING_OVERHEAD_TOKENS if model.reasoning else 0)
    pieces = math.ceil(tokens / model.max_output) if model.max_output else 1
    return model.ttft_ms / 1000 * max(1, pieces) + tokens / max(model.tokens_per_sec, 1.0)


class SpeedBook:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self._samples: dict[str, deque[Sample]] = {}
        self._prior: dict[str, tuple[int, float]] = {}

    def load(self, store: Store | None, days: float = LOAD_DAYS) -> int:
        """Read the most recent successful calls from the log. Returns how many were used."""
        if store is None:
            return 0
        rows = store.query(
            "SELECT model, ttft_ms, ms, output_tokens FROM calls WHERE status = 'ok' "
            "AND ms IS NOT NULL AND created_at >= ? ORDER BY created_at DESC LIMIT 5000",
            (time.time() - days * 86400,),
        )
        used = 0
        for row in reversed(rows):  # oldest first, so the newest stay in the window
            model = self.registry.get(row["model"])
            if model is None or row["ms"] is None:
                continue
            ttft = (row["ttft_ms"] if row["ttft_ms"] is not None else row["ms"]) / 1000
            self._add(model, ttft, row["ms"] / 1000, row["output_tokens"] or 0)
            used += 1
        for model_id in list(self._samples):
            model = self.registry.get(model_id)
            if model is not None:
                self._apply(model)
        return used

    def record(
        self, model: ModelInfo, ttft_s: float | None, total_s: float, out_tokens: int
    ) -> None:
        """One successful call: seconds to the first token, seconds in all, tokens out."""
        self._add(model, ttft_s if ttft_s is not None else total_s, total_s, out_tokens)
        self._apply(model)

    def _add(self, model: ModelInfo, ttft_s: float, total_s: float, out_tokens: int) -> None:
        self._prior.setdefault(model.id, (model.ttft_ms, model.tokens_per_sec))
        gen = total_s - ttft_s
        tps = out_tokens / gen if out_tokens >= MIN_TPS_TOKENS and gen >= MIN_GEN_S else None
        window = self._samples.setdefault(model.id, deque(maxlen=WINDOW))
        window.append(Sample(max(0.0, ttft_s), tps, total_s))

    def _apply(self, model: ModelInfo) -> None:
        """Write the blended figures onto the model, where the router reads them."""
        speed = self.speed(model)
        model.ttft_ms = round(speed.ttft_s * 1000)
        model.tokens_per_sec = speed.tps

    def speed(self, model: ModelInfo) -> Speed:
        prior_ttft_ms, prior_tps = self._prior.get(model.id, (model.ttft_ms, model.tokens_per_sec))
        samples = self._samples.get(model.id)
        if not samples:
            return Speed(prior_ttft_ms / 1000, prior_tps, 0)
        n = len(samples)
        weight = n / (n + PRIOR_SAMPLES)
        ttft = statistics.median(s.ttft_s for s in samples)
        ttft = prior_ttft_ms / 1000 * (1 - weight) + ttft * weight
        rates = [s.tps for s in samples if s.tps]
        if rates:
            w = len(rates) / (len(rates) + PRIOR_SAMPLES)
            tps = prior_tps * (1 - w) + statistics.median(rates) * w
        else:
            tps = prior_tps
        return Speed(ttft, max(tps, 1.0), n)

    def estimate(self, model: ModelInfo, out_tokens: int) -> float:
        return estimate_seconds(model, out_tokens)

    def table(self) -> list[dict[str, Any]]:
        """Measured figures for every model with calls, fastest first time to first token."""
        rows = []
        for model_id in self._samples:
            model = self.registry.get(model_id)
            if model is not None:
                rows.append({"model": model_id, **self.speed(model).as_dict()})
        return sorted(rows, key=lambda r: r["ttft_s"])
