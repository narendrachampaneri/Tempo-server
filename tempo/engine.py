"""The Tempo Brain: shared, long-lived parts (registry, router, health, quota, store, Laya,
cache) plus ``run``, which answers one question through the staged pipeline.

``Engine.run`` is an async stream of Events. Every client (web, CLI, OpenAI-compatible API)
consumes the same stream, so the thinking window shows exactly what the engine did.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tempo.accounts import Accounts, load_vault_key
from tempo.analyzer import analyze
from tempo.config import MAX_STAGES_LIMIT, Settings
from tempo.embeddings import EmbeddingClassifier, SemanticCache, load_encoder
from tempo.evals import SkillBook
from tempo.events import STREAM_EVENTS, Event
from tempo.health import HealthTracker
from tempo.laya_decider import LayaDecider
from tempo.mock import MockBackend
from tempo.prompts import TEMPO_SYSTEM
from tempo.providers import ChatBackend, LiteLLMBackend
from tempo.quota import QuotaManager
from tempo.registry import Registry
from tempo.router import Router, RouteResult
from tempo.sandbox import Limits, Sandbox, sandbox_home
from tempo.speed import SpeedBook
from tempo.store import Store
from tempo.sync import CATALOG_FILE, RegistrySync, load_catalog, save_catalog
from tempo.types import MODES, Access, ModelInfo, QueryProfile

if TYPE_CHECKING:
    from tempo.pipeline import Pipeline

DEFAULT_SYSTEM_PROMPT = TEMPO_SYSTEM
log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    mode: str = "auto"
    model: str | None = None  # try this model first for the draft (others remain fallbacks)
    allow_providers: list[str] | None = None
    local_only: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    max_attempts: int = 4  # models tried per slot before the slot gives up
    system_prompt: str | None = None  # added only if the conversation has no system message
    max_stages: int = 5
    time_budget_s: float = 60.0
    quota_budget: int = 12
    max_parallel: int = 3
    strategy: str | None = None  # force single / cascade / mixture / decompose
    # Stream answer text as it is produced. Clients that can replace text (web, CLI) say yes.
    live: bool = True
    # After some text was streamed, may a failure restart the answer on another model?
    restart_on_partial_failure: bool = True
    use_cache: bool = True
    access: Access = field(default_factory=Access)
    # Share of each model's daily free quota to leave untouched (tempo-server collect keeps some for
    # real users); 0 uses everything.
    quota_reserve: float = 0.0
    # Privacy "no_logging": never send to models whose free tier may log or train on prompts.
    no_logging: bool = False
    # Only models whose outputs may be training data (tempo-server collect --yes-only).
    training_only: bool = False
    # OpenAI request features: tool calling and structured output (tempo/compat.py).
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None
    # Model families never used for this run (second opinions and verification ask a family
    # other than the one that wrote the first answer).
    exclude_families: list[str] | None = None
    # Where the question came from, recorded with it: "mcp" for AI assistants over MCP (kept out
    # of training exports unless TEMPO_TRAIN_ON_MCP=1: they often carry private code or files).
    source: str | None = None
    # False: log nothing about this question (web Private chats). Also skips the answer cache.
    log: bool = True


@dataclass
class RunResult:
    text: str = ""
    reasoning: str = ""
    model: str | None = None
    question_id: str | None = None
    attempts: int = 0
    stages: int = 0
    requests: int = 0
    stop_reason: str | None = None
    score: float | None = None
    error: str | None = None
    error_kind: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def trace(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events if e.type not in STREAM_EVENTS]

    def apply(self, event: Event) -> None:
        """Fold one event into the result (answer_final is authoritative for the text)."""
        self.events.append(event)
        data = event.data
        if event.type == "answer_delta":
            self.text += data["delta"]
        elif event.type == "reasoning_delta":
            self.reasoning += data["delta"]
        elif event.type == "answer_reset":
            self.text = ""
            self.reasoning = ""
        elif event.type == "answer_final":
            self.text = data["answer"]
            self.reasoning = data.get("reasoning") or self.reasoning
            self.score = data.get("score")
            self.tool_calls = data.get("tool_calls") or []
        elif event.type == "received":
            self.question_id = data.get("question_id")
        elif event.type == "done":
            self.model = data["model"]
            self.attempts = data["attempts"]
            self.stages = data["stages"]
            self.requests = data["requests"]
            self.stop_reason = data["stop_reason"]
        elif event.type == "error":
            self.error = data["message"]
            self.error_kind = data.get("kind")


class Engine:
    def __init__(
        self,
        registry: Registry,
        backend_for: Callable[[ModelInfo], ChatBackend],
        *,
        settings: Settings | None = None,
        health: HealthTracker | None = None,
        store: Store | None = None,
        warm_up: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.settings = settings or Settings()
        self.registry = registry
        if self.settings.data_dir is not None:  # promoted Tempo-Core versions per task type
            from tempo.model_loop import load_routes

            registry.core_routes = load_routes(self.settings.data_dir)
        self.backend_for = backend_for
        self.health = health or HealthTracker()
        self.store = store or Store(self.settings.db_path)
        self.quota = QuotaManager(registry, self.store)
        self.accounts = Accounts(
            self.store, load_vault_key(self.settings.secret_key, self.settings.data_dir)
        )
        self.skills = SkillBook(registry, self.store)
        self.speed = SpeedBook(registry)  # measured time to first token and tokens a second
        self.router = Router(registry, self.health, self.quota, skill_of=self.skills.skill)
        # Runs code and maths answers in WebAssembly (tempo/sandbox.py); None when turned off.
        self.sandbox: Sandbox | None = None
        if self.settings.sandbox != "off":
            s = self.settings
            self.sandbox = Sandbox(
                sandbox_home(s.data_dir),
                Limits(
                    timeout_s=s.sandbox_timeout_s,
                    memory_mb=s.sandbox_memory_mb,
                    output_kb=s.sandbox_output_kb,
                ),
            )
        self.sync: RegistrySync | None = None  # periodic model-list sync (from_settings)
        self._maintenance: asyncio.Task[None] | None = None
        self.cache: SemanticCache | None = None  # attached in Engine.from_settings
        self.laya: LayaDecider | None = None  # attached in Engine.from_settings
        self._laya_loading: Any = None
        self.classifier: EmbeddingClassifier | None = None
        self._classifier_loading: Any = None
        self._warm_up = warm_up
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> Engine:
        registry = Registry.load(settings.models_file, include_mock=settings.enable_mock)
        litellm_backend = LiteLLMBackend(registry, timeout=settings.request_timeout)
        mock_backend = MockBackend()

        def backend_for(model: ModelInfo) -> ChatBackend:
            return mock_backend if model.provider == "mock" else litellm_backend

        engine = cls(registry, backend_for, settings=settings, warm_up=litellm_backend.warm_up)
        engine.laya = LayaDecider(settings, engine.store)
        engine.sync = RegistrySync(registry, engine.health)
        model_name = settings.embedding_model
        classifier = EmbeddingClassifier(
            lambda: load_encoder(settings.embeddings, model_name),
            multilingual="multilingual" in (model_name or "").lower(),
        )
        engine.classifier = classifier
        if settings.cache:
            engine.cache = SemanticCache(lambda: classifier.encoder, ttl_s=settings.cache_ttl_s)
        return engine

    async def startup(self, oneshot: bool = False) -> None:
        """One-time async setup: discover local Ollama models and load provider libraries.

        A server (``oneshot=False``) loads Laya and the embedding classifier in the background
        (questions meanwhile use the rules) and starts the periodic registry sync. A one-shot
        CLI run loads the classifier before answering, skips Laya unless TEMPO_LAYA=on, and
        does not sync.
        """
        if self.settings.data_dir is not None:
            load_catalog(self.registry, self.settings.data_dir / CATALOG_FILE)
        await self.registry.discover_ollama()
        self.speed.load(self.store)
        loop = asyncio.get_running_loop()
        if self.laya is not None:
            if oneshot and self.settings.laya != "on":
                self.laya.status = "off"
            else:
                self._laya_loading = self.laya.start()
        if self.classifier is not None and self.classifier.status == "loading":
            self._classifier_loading = loop.run_in_executor(None, self.classifier.load)
            if oneshot:
                await self._classifier_loading
        if not oneshot and self.sync is not None and self.settings.sync_interval_s > 0:
            self._maintenance = asyncio.create_task(self._sync_loop())
        if self._warm_up:
            await self._warm_up()

    async def _sync_loop(self) -> None:
        """Refresh provider model lists (and check provider health) now and then."""
        assert self.sync is not None
        from tempo.sync import HEALTH_EVERY_S

        last_full = -math.inf
        while True:
            try:
                if time.monotonic() - last_full >= self.settings.sync_interval_s:
                    status = await self.sync.run(self.listing_keys())
                    last_full = time.monotonic()
                    self.save_catalog(status)
                else:  # between full syncs: OpenRouter endpoint health only
                    await self.sync.check_openrouter_health()
            except Exception:  # a sync problem must never take the server down
                log.exception("registry sync failed")
            await asyncio.sleep(min(self.settings.sync_interval_s, HEALTH_EVERY_S))

    def retry_after_s(self, access: Access | None = None) -> int:
        """When to try again after "no model available": 60 s if only per-minute limits or
        cool-downs block, else the next daily reset among the configured providers."""
        access = access or Access()
        best: float | None = None
        for model in self.registry.all():
            provider = self.registry.providers[model.provider]
            if provider.local or not model.chat_capable:
                continue
            if not self.registry.is_configured(model.provider, access):
                continue
            reason = self.quota.blocked_reason(model, access.key_id(model.provider))
            if reason is None or "/min" in reason:
                wait = 60.0
            else:
                wait = self.quota.seconds_to_day_reset(model.provider)
            best = wait if best is None else min(best, wait)
        return max(1, int(best if best is not None else 60))

    def listing_keys(self) -> dict[str, str]:
        """Keys used only to read providers' model lists: the owner's keys from the vault (what
        `tempo-server setup` stores), and for providers that take only users' own keys, one
        user's."""
        from tempo.accounts import ADMIN_USER, LOCAL_USER

        byok = {p.id for p in self.registry.providers.values() if p.byok_only}
        keys: dict[str, str] = {}
        for owner in (LOCAL_USER, ADMIN_USER):
            for provider, key in self.accounts.keys(owner).items():
                if provider not in byok:
                    keys.setdefault(provider, key)
        for row in self.store.query("SELECT DISTINCT user_id, provider FROM user_keys"):
            if row["provider"] in byok and row["provider"] not in keys:
                key = self.accounts.keys(row["user_id"]).get(row["provider"])
                if key:
                    keys[row["provider"]] = key
        return keys

    async def refresh_provider(self, provider: str, api_key: str) -> None:
        """A user just added a key: read that provider's model list with it."""
        if self.sync is None:
            self.sync = RegistrySync(self.registry, self.health)
        try:
            status = await self.sync.run({provider: api_key}, only={provider})
            self.save_catalog(status)
        except Exception:  # a list that can't be read must never fail adding a key
            log.exception("model list refresh for %s failed", provider)

    def save_catalog(self, status: dict[str, Any]) -> None:
        if self.settings.data_dir is not None:
            save_catalog(self.registry, status, self.settings.data_dir / CATALOG_FILE)

    def access_for(self, user_id: str) -> Access:
        """The credentials a user's requests run with: their own keys, then the server's. The
        admin (TEMPO_API_KEY holder) is the owner, so also gets the keys `tempo-server setup`
        stored."""
        from tempo.accounts import ADMIN_USER, LOCAL_USER

        keys = self.accounts.keys(user_id)
        if user_id == ADMIN_USER:
            keys = {**self.accounts.keys(LOCAL_USER), **keys}
        return Access(user_id=user_id, user_keys=keys)

    def options(self, **overrides: Any) -> RunOptions:
        s = self.settings
        values: dict[str, Any] = {
            "max_attempts": s.max_attempts,
            "max_stages": s.max_stages,
            "time_budget_s": s.time_budget_s,
            "quota_budget": s.quota_budget,
            "max_parallel": s.max_parallel,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        values["max_stages"] = max(1, min(MAX_STAGES_LIMIT, int(values["max_stages"])))
        values["max_parallel"] = max(1, int(values["max_parallel"]))
        values["quota_budget"] = max(1, int(values["quota_budget"]))
        values["time_budget_s"] = max(1.0, float(values["time_budget_s"]))
        if values.get("mode") not in MODES:
            values["mode"] = "auto"
        return RunOptions(**values)

    # --- hooks used by the pipeline ---------------------------------------------------------

    async def understand(self, messages: list[dict[str, Any]]) -> tuple[QueryProfile, str]:
        """Profile the request: rules first, refined by the embedding classifier if present."""
        profile = analyze(messages)
        source = "rules"
        if self.classifier is not None:
            profile, source = await self.classifier.refine(messages, profile)
        return profile, source

    async def decide(
        self, pipeline: Pipeline, name: str, stage: int, rules_value: Any, **context: Any
    ) -> Any:
        """Make one routing decision. Rules decide unless Laya has taken this decision over."""
        if self.laya is not None:
            return await self.laya.decide(pipeline, name, stage, rules_value, **context)
        return rules_value

    def no_model_message(self, route: RouteResult) -> str:
        reasons = route.skipped_summary()
        if set(reasons) <= {"provider not configured"}:
            return (
                "No model providers are configured. Run `tempo-server setup` to add your free keys "
                "(or set them in .env), or start Ollama for local models."
            )
        if any("used up" in reason for reason in reasons):
            return (
                "Free quota is used up on every provider for now, and no local model is "
                "running. Start Ollama for local answers (tempo-server setup), or try again later "
                "(see Retry-After and `tempo-server quota`)."
            )
        detail = ", ".join(f"{n} {reason}" for reason, n in reasons.items())
        return f"No available model can handle this request ({detail})."

    # --- running questions -------------------------------------------------------------------

    async def run(
        self, messages: Iterable[Mapping[str, Any]], options: RunOptions | None = None
    ) -> AsyncIterator[Event]:
        from tempo.pipeline import Pipeline

        pipeline = Pipeline(self, list(messages), options or self.options(), uuid.uuid4().hex)
        if self.laya is not None:
            self.laya.question_started()
        try:
            async for event in pipeline.events():
                yield event
        finally:
            if self.laya is not None:
                self.laya.question_finished()

    async def complete(
        self, messages: Iterable[Mapping[str, Any]], options: RunOptions | None = None
    ) -> RunResult:
        return await collect(self.run(messages, options))


async def collect(events: AsyncIterator[Event]) -> RunResult:
    result = RunResult()
    async for event in events:
        result.apply(event)
    return result
