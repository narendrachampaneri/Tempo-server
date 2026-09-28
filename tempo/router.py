"""Router (generation 1): hard filters, then a utility score per model.

    utility = w_quality * predicted_quality - w_scarcity * scarcity - w_latency * latency

``predicted_quality`` comes from registry skill priors. Later generations swap in a
learned predictor (kNN, two-tower network, GraphRouter) behind the same interface.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from tempo.health import HealthTracker
from tempo.registry import Registry
from tempo.types import ModelInfo, QueryProfile


@dataclass(frozen=True)
class Weights:
    quality: float
    scarcity: float
    latency: float


MODE_WEIGHTS: dict[str, Weights] = {
    "auto": Weights(quality=0.70, scarcity=0.15, latency=0.15),
    "fast": Weights(quality=0.50, scarcity=0.10, latency=0.40),
    "best": Weights(quality=0.95, scarcity=0.00, latency=0.05),
    "private": Weights(quality=0.70, scarcity=0.15, latency=0.15),
}

# Extra hidden "thinking" tokens a reasoning model spends before answering.
REASONING_OVERHEAD_TOKENS = 300


@dataclass
class Candidate:
    model: ModelInfo
    utility: float
    quality: float
    latency_s: float
    scarcity: float
    why: str = ""


@dataclass
class RouteResult:
    candidates: list[Candidate]
    skipped: dict[str, list[str]] = field(default_factory=dict)

    def skipped_summary(self) -> dict[str, int]:
        return {reason: len(ids) for reason, ids in self.skipped.items()}


def predicted_quality(model: ModelInfo, profile: QueryProfile) -> float:
    skill = model.skill(profile.task)
    if profile.script != "latin":
        # Non-Latin input: lean on the multilingual ability measured by the translate skill.
        skill = 0.7 * skill + 0.3 * model.skill("translate")
    c = profile.complexity
    # Easy queries depend on task skill; hard ones increasingly on raw model strength.
    quality = skill * (1 - 0.4 * c) + model.strength * 0.4 * c
    if "reasoning" in profile.needs and model.reasoning:
        quality += 0.05
    return round(min(1.0, quality), 4)


def expected_latency(model: ModelInfo, profile: QueryProfile) -> float:
    tokens = profile.est_output_tokens + (REASONING_OVERHEAD_TOKENS if model.reasoning else 0)
    return model.ttft_ms / 1000 + tokens / max(model.tokens_per_sec, 1.0)


def scarcity(model: ModelInfo) -> float:
    """0 = unlimited (local), ~0.17 at 14,400 requests/day, ~0.66 at 50/day."""
    if model.free_rpd is None:
        return 0.0
    return min(1.0, max(0.0, 1 - math.log10(max(model.free_rpd, 1)) / 5))


def _latency_penalty(seconds: float) -> float:
    # Log scale: going from 1 s to 3 s hurts more than from 20 s to 22 s. 30 s maps to 1.0.
    return min(1.0, math.log10(1 + seconds) / math.log10(31))


def score(model: ModelInfo, profile: QueryProfile, weights: Weights) -> Candidate:
    quality = predicted_quality(model, profile)
    latency = expected_latency(model, profile)
    scarce = scarcity(model)
    utility = (
        weights.quality * quality
        - weights.scarcity * scarce
        - weights.latency * _latency_penalty(latency)
    )
    return Candidate(
        model=model,
        utility=round(utility, 4),
        quality=quality,
        latency_s=round(latency, 2),
        scarcity=round(scarce, 3),
    )


class Router:
    def __init__(self, registry: Registry, health: HealthTracker) -> None:
        self.registry = registry
        self.health = health

    def skip_reason(
        self,
        model: ModelInfo,
        profile: QueryProfile,
        *,
        local_only: bool = False,
        allow_providers: Iterable[str] | None = None,
    ) -> str | None:
        provider = self.registry.providers[model.provider]
        if not self.registry.is_configured(model.provider):
            return "provider not configured"
        if model.installed is False:
            return "not installed"
        if local_only and not provider.local:
            return "not local"
        if allow_providers is not None and model.provider not in set(allow_providers):
            return "provider not allowed"
        reason = self.health.unavailable_reason(model)
        if reason:
            return reason
        if profile.has_images and not model.vision:
            return "no image support"
        needed = profile.input_tokens + profile.est_output_tokens
        if needed > model.context_window:
            return "context window too small"
        if model.free_tpm is not None and needed > model.free_tpm:
            return "request exceeds free tokens/min"
        return None

    def candidate(self, model: ModelInfo, profile: QueryProfile, mode: str = "auto") -> Candidate:
        """Score one specific model (used when the caller asks for a model by name)."""
        return score(model, profile, MODE_WEIGHTS.get(mode, MODE_WEIGHTS["auto"]))

    def rank(
        self,
        profile: QueryProfile,
        *,
        mode: str = "auto",
        allow_providers: Iterable[str] | None = None,
        local_only: bool = False,
    ) -> RouteResult:
        weights = MODE_WEIGHTS.get(mode, MODE_WEIGHTS["auto"])
        local_only = local_only or mode == "private"
        allowed = list(allow_providers) if allow_providers is not None else None

        candidates: list[Candidate] = []
        skipped: dict[str, list[str]] = {}
        for model in self.registry.all():
            reason = self.skip_reason(
                model, profile, local_only=local_only, allow_providers=allowed
            )
            if reason:
                skipped.setdefault(reason, []).append(model.id)
                continue
            candidates.append(score(model, profile, weights))

        candidates.sort(key=lambda c: (-c.utility, c.model.id))
        for index, candidate in enumerate(candidates):
            others = candidates[:index] + candidates[index + 1 :]
            candidate.why = _explain(candidate, others, profile)
        return RouteResult(candidates=candidates, skipped=skipped)


def _explain(best: Candidate, others: list[Candidate], profile: QueryProfile) -> str:
    """Why this model beat the others (the route event shows its quota separately)."""
    parts: list[str] = []
    if all(best.quality >= o.quality for o in others):
        parts.append(f"highest predicted quality for {profile.task}")
    if all(best.latency_s <= o.latency_s for o in others):
        parts.append("fastest")
    if others and all(best.scarcity < o.scarcity for o in others):
        parts.append("most free quota")
    if not parts:
        parts.append("best balance of quality, speed and free quota")
    return ", ".join(parts)
