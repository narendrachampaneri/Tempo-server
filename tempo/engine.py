"""The Tempo Brain (Phase 1): understand → plan → route → execute with fallback.

``Engine.run`` is an async stream of Events. Every client (web, CLI, OpenAI-compatible API)
consumes the same stream, so the thinking window shows exactly what the engine did.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tempo.analyzer import analyze
from tempo.config import Settings
from tempo.events import STREAM_EVENTS, Event
from tempo.health import HealthTracker
from tempo.mock import MockBackend
from tempo.providers import ERROR_LABELS, ChatBackend, LiteLLMBackend, ProviderError
from tempo.quota import QuotaManager
from tempo.registry import Registry
from tempo.router import Candidate, Router, RouteResult
from tempo.store import Store
from tempo.types import MODES, ModelInfo, QueryProfile

DEFAULT_SYSTEM_PROMPT = (
    "You are Tempo, a helpful assistant. Answer accurately and concisely. Use Markdown, and "
    "put code in fenced code blocks. If you are not sure about something, say so."
)


@dataclass
class RunOptions:
    mode: str = "auto"
    model: str | None = None  # ask for a specific model first (others remain as fallbacks)
    allow_providers: list[str] | None = None
    local_only: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    max_attempts: int = 4
    system_prompt: str | None = None  # added only if the conversation has no system message
    # After some answer text was streamed, may a failure restart the answer on another model?
    # Clients that can clear partial output (web, CLI) say yes; plain OpenAI streams say no.
    restart_on_partial_failure: bool = True


@dataclass
class RunResult:
    text: str = ""
    reasoning: str = ""
    model: str | None = None
    attempts: int = 0
    error: str | None = None
    error_kind: str | None = None
    events: list[Event] = field(default_factory=list)

    @property
    def trace(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events if e.type not in STREAM_EVENTS]


class Engine:
    def __init__(
        self,
        registry: Registry,
        backend_for: Callable[[ModelInfo], ChatBackend],
        *,
        health: HealthTracker | None = None,
        store: Store | None = None,
        max_attempts: int = 4,
        warm_up: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.registry = registry
        self.backend_for = backend_for
        self.health = health or HealthTracker()
        self.store = store or Store()
        self.quota = QuotaManager(registry, self.store)
        self.router = Router(registry, self.health, self.quota)
        self.max_attempts = max_attempts
        self._warm_up = warm_up
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> Engine:
        registry = Registry.load(settings.models_file, include_mock=settings.enable_mock)
        litellm_backend = LiteLLMBackend(registry, timeout=settings.request_timeout)
        mock_backend = MockBackend()

        def backend_for(model: ModelInfo) -> ChatBackend:
            return mock_backend if model.provider == "mock" else litellm_backend

        return cls(
            registry,
            backend_for,
            max_attempts=settings.max_attempts,
            warm_up=litellm_backend.warm_up,
        )

    async def startup(self) -> None:
        """One-time async setup: discover local Ollama models and load provider libraries."""
        await self.registry.discover_ollama()
        if self._warm_up:
            await self._warm_up()

    def options(self, **overrides: Any) -> RunOptions:
        return RunOptions(max_attempts=self.max_attempts, **overrides)

    async def run(
        self, messages: Iterable[Mapping[str, Any]], options: RunOptions | None = None
    ) -> AsyncIterator[Event]:
        options = options or self.options()
        start = self._clock()

        def ev(event_type: str, /, **data: Any) -> Event:
            return Event(event_type, round(self._clock() - start, 3), data)

        mode = options.mode if options.mode in MODES else "auto"
        yield ev("received", mode=mode)

        messages = [dict(m) for m in messages]
        if not any(m.get("role") == "user" for m in messages):
            yield ev("error", message="There is no user message to answer.", kind="bad_request")
            return

        profile = analyze(messages)
        yield ev(
            "analyze",
            task=profile.task,
            complexity=profile.complexity,
            script=profile.script,
            needs=profile.needs,
            input_tokens=profile.input_tokens,
            est_output_tokens=profile.est_output_tokens,
        )

        route = self.router.rank(
            profile,
            mode=mode,
            allow_providers=options.allow_providers,
            local_only=options.local_only,
        )
        ordered = route.candidates
        if options.model:
            requested = self.registry.get(options.model)
            if requested is None:
                yield ev("error", message=f"Unknown model: {options.model}", kind="not_found")
                return
            reason = self.router.skip_reason(
                requested,
                profile,
                local_only=options.local_only or mode == "private",
                allow_providers=options.allow_providers,
            )
            if reason:
                message = f"{options.model} is unavailable: {reason}"
                yield ev("error", message=message, kind="unavailable")
                return
            chosen = self.router.candidate(requested, profile, mode)
            chosen.why = "requested by caller"
            ordered = [chosen] + [c for c in ordered if c.model.id != requested.id]

        if not ordered:
            yield ev(
                "error",
                message=self._no_model_message(route),
                kind="unavailable",
                skipped=route.skipped_summary(),
            )
            return

        yield ev(
            "plan",
            strategy="single",
            max_attempts=min(options.max_attempts, len(ordered)),
            candidates=len(ordered),
        )
        yield ev(
            "route",
            **self._route_data(ordered[0], route, profile),
            alternatives=[{"model": c.model.id, "utility": c.utility} for c in ordered[1:3]],
        )

        call_messages = _with_system_prompt(messages, options.system_prompt)
        attempts = 0
        tried: list[str] = []
        last_error: ProviderError | None = None

        for candidate in ordered:
            if attempts >= options.max_attempts:
                break
            model = candidate.model
            if attempts and self.health.unavailable_reason(model):
                continue  # e.g. its provider's key was rejected by an earlier attempt
            if attempts:
                yield ev("fallback", **{"from": tried[-1]}, to=model.id, why=candidate.why)
            attempts += 1
            tried.append(model.id)
            yield ev("call_start", model=model.id, attempt=attempts)

            call_start = self._clock()
            first_token: float | None = None
            answer_text = ""
            streamed = False  # any answer text sent to the client
            visible = False  # any non-whitespace answer text
            try:
                meta: dict[str, Any] = {}
                stream = self.backend_for(model).stream(
                    model,
                    call_messages,
                    temperature=options.temperature,
                    max_tokens=options.max_tokens,
                    meta=meta,
                )
                async for kind, text in stream:
                    if first_token is None:
                        first_token = self._clock()
                    if kind == "reasoning":
                        yield ev("reasoning_delta", model=model.id, delta=text)
                        continue
                    streamed = True
                    answer_text += text
                    visible = visible or bool(text.strip())
                    yield ev("answer_delta", model=model.id, delta=text)
                if not visible:
                    raise ProviderError("empty", "The model returned no answer text.")
            except ProviderError as err:
                last_error = err
                self.health.record_failure(model, err.kind, err.retry_after)
                self._record_usage(model, profile, "", meta)
                yield ev("call_error", model=model.id, kind=err.kind, message=err.message)
                if streamed:
                    if not options.restart_on_partial_failure:
                        yield ev(
                            "error",
                            message=f"{model.id} failed mid-answer: {ERROR_LABELS[err.kind]}",
                            kind=err.kind,
                        )
                        return
                    yield ev("answer_reset", model=model.id)
                continue

            self.health.record_success(model)
            self._record_usage(model, profile, answer_text, meta)
            now = self._clock()
            yield ev(
                "call_end",
                model=model.id,
                ms=round((now - call_start) * 1000),
                ttft_ms=round((first_token - call_start) * 1000) if first_token else None,
            )
            yield ev(
                "done",
                model=model.id,
                attempts=attempts,
                total_ms=round((now - start) * 1000),
            )
            return

        label = ERROR_LABELS.get(last_error.kind, "failed") if last_error else "no model available"
        yield ev(
            "error",
            message=f"All {attempts} attempted model(s) failed (last: {label}).",
            kind=last_error.kind if last_error else "unavailable",
            tried=tried,
        )

    async def complete(
        self, messages: Iterable[Mapping[str, Any]], options: RunOptions | None = None
    ) -> RunResult:
        return await collect(self.run(messages, options))

    def _record_usage(
        self, model: ModelInfo, profile: QueryProfile, answer: str, meta: dict[str, Any]
    ) -> None:
        tokens = profile.input_tokens + len(answer) // 4
        self.quota.record(model, "server", tokens)
        self.quota.observe_headers(model, "server", meta.get("headers"))

    def _route_data(
        self, candidate: Candidate, route: RouteResult, profile: QueryProfile
    ) -> dict[str, Any]:
        model = candidate.model
        return {
            "model": model.id,
            "name": model.name,
            "provider": model.provider,
            "why": candidate.why,
            "utility": candidate.utility,
            "quality": candidate.quality,
            "latency_s": candidate.latency_s,
            "free_rpd": model.free_rpd,
            "local": self.registry.providers[model.provider].local,
            "skipped": route.skipped_summary(),
        }

    def _no_model_message(self, route: RouteResult) -> str:
        reasons = route.skipped_summary()
        if set(reasons) <= {"provider not configured"}:
            return (
                "No model providers are configured. Set at least one free API key "
                "(GROQ_API_KEY, CEREBRAS_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY) "
                "or OLLAMA_API_BASE. See .env.example."
            )
        detail = ", ".join(f"{n} {reason}" for reason, n in reasons.items())
        return f"No available model can handle this request ({detail})."


def _with_system_prompt(
    messages: list[dict[str, Any]], system_prompt: str | None
) -> list[dict[str, Any]]:
    if not system_prompt or any(m.get("role") == "system" for m in messages):
        return messages
    return [{"role": "system", "content": system_prompt}, *messages]


async def collect(events: AsyncIterator[Event]) -> RunResult:
    result = RunResult()
    async for event in events:
        result.events.append(event)
        data = event.data
        if event.type == "answer_delta":
            result.text += data["delta"]
        elif event.type == "reasoning_delta":
            result.reasoning += data["delta"]
        elif event.type == "answer_reset":
            result.text = ""
            result.reasoning = ""
        elif event.type == "call_start":
            result.attempts = data["attempt"]
            result.reasoning = ""  # reasoning belongs to the model that produces the answer
        elif event.type == "done":
            result.model = data["model"]
        elif event.type == "error":
            result.error = data["message"]
            result.error_kind = data.get("kind")
    return result
