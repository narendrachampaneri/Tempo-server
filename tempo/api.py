"""HTTP API: the web app, an OpenAI-compatible endpoint, and a trace-event stream."""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from tempo import __version__
from tempo.accounts import ADMIN_USER, LOCAL_USER
from tempo.config import MAX_STAGES_LIMIT, Settings
from tempo.engine import DEFAULT_SYSTEM_PROMPT, Engine, RunOptions
from tempo.events import STREAM_EVENTS
from tempo.sync import verify_key
from tempo.types import MODES, Access, ModelInfo

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
ERROR_STATUS = {"bad_request": 400, "not_found": 404}


class APIError(Exception):
    def __init__(
        self, status: int, message: str, *, kind: str = "invalid_request_error", code: str | None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.kind = kind
        self.code = code

    def body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.kind, "code": self.code}}


class StageOptions(BaseModel):
    """Per-question budgets for the staged engine; unset fields use the server settings."""

    max_stages: int | None = Field(default=None, ge=1, le=MAX_STAGES_LIMIT)
    time_budget_s: float | None = Field(default=None, ge=1, le=600)
    quota_budget: int | None = Field(default=None, ge=1, le=200)
    max_parallel: int | None = Field(default=None, ge=1, le=8)
    strategy: Literal["single", "cascade", "mixture", "decompose"] | None = None

    def stage_overrides(self) -> dict[str, Any]:
        return {
            "max_stages": self.max_stages,
            "time_budget_s": self.time_budget_s,
            "quota_budget": self.quota_budget,
            "max_parallel": self.max_parallel,
            "strategy": self.strategy,
        }


class TempoOptions(StageOptions):
    """Tempo-specific request options ("conditions"), sent as the ``tempo`` field."""

    mode: str | None = None
    privacy: Literal["default", "local_only"] = "default"
    allow_providers: list[str] | None = None
    trace: bool = False


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "tempo/auto"
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    tempo: TempoOptions = Field(default_factory=TempoOptions)


class AskRequest(StageOptions):
    prompt: str | None = None
    messages: list[dict[str, Any]] | None = None
    mode: str = "auto"
    model: str | None = None
    privacy: Literal["default", "local_only"] = "default"
    allow_providers: list[str] | None = None


class KeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=500, repr=False)
    verify: bool = True


class FeedbackRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    rating: Literal[1, -1]
    comment: str | None = Field(default=None, max_length=2000)


def _current_access(request: Request) -> Access:
    """Whose keys this request uses; create_app installs the resolver on app.state."""
    return request.app.state.access_for(request)


AccessDep = Annotated[Access, Depends(_current_access)]


def _sse(payload: Any) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _pieces(text: str, size: int = 120) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def create_app(engine: Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or Engine.from_settings(settings)
    web_page = resources.files("tempo").joinpath("web/index.html").read_text(encoding="utf-8")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await engine.startup()
        yield

    app = FastAPI(title="Tempo", version=__version__, lifespan=lifespan)
    app.state.engine = engine

    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    def require_key(request: Request, authorization: str | None = Header(default=None)) -> None:
        """Identify the caller. TEMPO_API_KEY is the admin; users have their own keys; with
        neither configured the server runs in single-user local mode without auth."""
        token = (authorization or "").removeprefix("Bearer ").strip()
        if settings.api_key and hmac.compare_digest(token.encode(), settings.api_key.encode()):
            request.state.user_id, request.state.user_name = ADMIN_USER, ADMIN_USER
            return
        user = engine.accounts.authenticate(token) if token else None
        if user is not None:
            request.state.user_id, request.state.user_name = user.id, user.name
            return
        if settings.api_key or engine.accounts.has_users():
            raise APIError(401, "Invalid or missing API key.", code="invalid_api_key")
        request.state.user_id, request.state.user_name = LOCAL_USER, LOCAL_USER

    def access_for(request: Request) -> Access:
        return engine.access_for(getattr(request.state, "user_id", LOCAL_USER))

    app.state.access_for = access_for

    def model_status(model: ModelInfo, access: Access | None = None) -> str:
        if not engine.registry.is_configured(model.provider, access):
            return "not configured"
        if model.installed is False:
            return "not installed"
        if model.listed is False:
            return "no longer offered"
        return engine.health.unavailable_reason(model) or "ready"

    def ready_models(access: Access | None = None) -> list[ModelInfo]:
        return [m for m in engine.registry.all() if model_status(m, access) == "ready"]

    def resolve_model(name: str) -> tuple[str, str | None]:
        """Map an OpenAI ``model`` field to (mode, explicit model id)."""
        if name in ("tempo", "tempo/auto"):
            return "auto", None
        if name.startswith("tempo/"):
            mode = name.split("/", 1)[1]
            if mode in MODES:
                return mode, None
        elif engine.registry.get(name):
            return "auto", name
        raise APIError(404, f"The model `{name}` does not exist.", code="model_not_found")

    # --- public --------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(web_page, headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "models_ready": len(ready_models())}

    # --- OpenAI-compatible ---------------------------------------------------

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_key)])

    @v1.get("/models")
    async def list_models(access: AccessDep) -> dict[str, Any]:
        virtual = [
            {"id": f"tempo/{mode}", "object": "model", "created": 0, "owned_by": "tempo"}
            for mode in MODES
        ]
        concrete = [
            {"id": m.id, "object": "model", "created": 0, "owned_by": m.provider}
            for m in ready_models(access)
        ]
        return {"object": "list", "data": virtual + concrete}

    @v1.post("/chat/completions", response_model=None)
    async def chat_completions(
        req: ChatCompletionRequest, access: AccessDep
    ) -> JSONResponse | StreamingResponse:
        mode, explicit = resolve_model(req.model)
        mode = req.tempo.mode if req.tempo.mode in MODES else mode
        options: RunOptions = engine.options(
            mode=mode,
            model=explicit,
            allow_providers=req.tempo.allow_providers,
            local_only=req.tempo.privacy == "local_only",
            temperature=req.temperature,
            max_tokens=req.max_completion_tokens or req.max_tokens,
            access=access,
            **req.tempo.stage_overrides(),
        )
        # OpenAI clients cannot take back streamed text, so a stream carries the checked final
        # answer; only a one-stage request (nothing to replace it) streams tokens as they come.
        options.live = req.stream and options.max_stages == 1
        options.restart_on_partial_failure = not options.live
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        if not req.stream:
            result = await engine.complete(req.messages, options)
            if result.error:
                kind = result.error_kind or "unavailable"
                raise APIError(
                    ERROR_STATUS.get(kind, 503), result.error, kind="tempo_error", code=kind
                )
            tempo_info: dict[str, Any] = {
                "requested_model": req.model,
                "routed_to": result.model,
                "question_id": result.question_id,
                "stages": result.stages,
                "attempts": result.attempts,
                "requests": result.requests,
                "stop_reason": result.stop_reason,
                "score": result.score,
            }
            if req.tempo.trace:
                tempo_info["trace"] = result.trace
            message: dict[str, Any] = {"role": "assistant", "content": result.text}
            if result.reasoning:
                message["reasoning_content"] = result.reasoning
            return JSONResponse(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": result.model,
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "tempo": tempo_info,
                }
            )

        async def stream() -> AsyncIterator[str]:
            label = req.model
            role_sent = False

            def chunk(delta: dict[str, Any] | None, finish: str | None = None) -> dict[str, Any]:
                choices = (
                    [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
                )
                return {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": label,
                    "choices": choices,
                }

            async for event in engine.run(req.messages, options):
                if event.type in ("call_end", "answer_final") and event.data.get("model"):
                    label = event.data["model"]

                if event.type in STREAM_EVENTS:
                    if not role_sent:
                        yield _sse(chunk({"role": "assistant", "content": ""}))
                        role_sent = True
                    field = "content" if event.type == "answer_delta" else "reasoning_content"
                    yield _sse(chunk({field: event.data["delta"]}))
                elif event.type == "answer_final" and not options.live:
                    if not role_sent:
                        yield _sse(chunk({"role": "assistant", "content": ""}))
                        role_sent = True
                    for piece in _pieces(event.data["answer"]):
                        yield _sse(chunk({"content": piece}))
                    if req.tempo.trace:
                        yield _sse({**chunk(None), "tempo": {"event": event.to_dict()}})
                elif event.type == "done":
                    yield _sse(chunk({}, finish="stop"))
                elif event.type == "error":
                    error = APIError(
                        503,
                        event.data["message"],
                        kind="tempo_error",
                        code=event.data.get("kind") or "unavailable",
                    )
                    yield _sse(error.body())
                    return
                elif req.tempo.trace:
                    # Trace events ride along as chunks with no choices, which OpenAI clients skip.
                    yield _sse({**chunk(None), "tempo": {"event": event.to_dict()}})
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    # --- native API for the web app and CLI ----------------------------------

    api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])

    @api.post("/ask")
    async def ask(req: AskRequest, access: AccessDep) -> StreamingResponse:
        messages = req.messages or []
        if req.prompt:
            messages = [*messages, {"role": "user", "content": req.prompt}]
        if not messages:
            raise APIError(400, "Send `prompt` or `messages`.", code="empty_request")
        options = engine.options(
            mode=req.mode if req.mode in MODES else "auto",
            model=req.model or None,
            allow_providers=req.allow_providers,
            local_only=req.privacy == "local_only",
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            access=access,
            **req.stage_overrides(),
        )

        async def stream() -> AsyncIterator[str]:
            async for event in engine.run(messages, options):
                yield _sse(event.to_dict())

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @api.post("/feedback")
    async def feedback(req: FeedbackRequest, request: Request) -> dict[str, Any]:
        question = engine.store.question(req.question_id)
        owner = request.state.user_id
        if question is None or (owner != ADMIN_USER and question["user_id"] != owner):
            raise APIError(404, "Unknown question id.", code="question_not_found")
        engine.store.set_feedback(req.question_id, req.rating, req.comment)
        return {"ok": True}

    @api.get("/me")
    async def me(request: Request) -> dict[str, Any]:
        user_id = request.state.user_id
        mode = "local" if user_id == LOCAL_USER else "admin" if user_id == ADMIN_USER else "user"
        return {"user": request.state.user_name, "mode": mode}

    @api.get("/keys")
    async def list_keys(request: Request) -> dict[str, Any]:
        stored = {k["provider"]: k for k in engine.accounts.key_info(request.state.user_id)}
        registry = engine.registry
        providers = []
        for p in registry.providers.values():
            if not p.key_env:
                continue  # local providers and demo models take no key
            mine = stored.get(p.id)
            providers.append(
                {
                    "provider": p.id,
                    "label": p.label,
                    "signup_url": p.signup_url,
                    "free_tier": p.free_tier,
                    "free_tier_note": p.free_tier_note,
                    "has_key": mine is not None,
                    "last4": mine["last4"] if mine else None,
                    "verified": None
                    if not mine or mine["verified"] is None
                    else bool(mine["verified"]),
                    "server_key": registry.is_configured(p.id),
                }
            )
        return {"providers": providers}

    @api.put("/keys/{provider}")
    async def put_key(provider: str, req: KeyRequest, request: Request) -> dict[str, Any]:
        info = engine.registry.providers.get(provider)
        if info is None or not info.key_env:
            raise APIError(404, f"Unknown provider {provider!r}.", code="provider_not_found")
        verified = None
        if req.verify:
            verified = await verify_key(engine.registry, provider, req.api_key.strip())
            if verified is False:
                raise APIError(400, f"{info.label} rejected this key.", code="key_rejected")
        engine.accounts.set_key(request.state.user_id, provider, req.api_key, verified)
        return {"ok": True, "provider": provider, "verified": verified}

    @api.delete("/keys/{provider}")
    async def delete_key(provider: str, request: Request) -> dict[str, Any]:
        if not engine.accounts.delete_key(request.state.user_id, provider):
            raise APIError(404, f"No stored key for {provider!r}.", code="key_not_found")
        return {"ok": True}

    @api.get("/usage")
    async def usage(request: Request, access: AccessDep, hours: int = 24) -> dict[str, Any]:
        """Dashboard numbers for the last ``hours``. Users see their own questions; the local
        user and the admin see everything."""
        hours = max(1, min(hours, 24 * 90))
        since = time.time() - hours * 3600
        user_id = request.state.user_id
        mine = user_id not in (LOCAL_USER, ADMIN_USER)
        where = "created_at >= ?" + (" AND user_id = ?" if mine else "")
        params: tuple[Any, ...] = (since, user_id) if mine else (since,)
        store = engine.store
        questions = store.query(
            "SELECT id, stages_used, requests_used, total_ms, stop_reason, cache_hit, feedback, "
            f"error FROM questions WHERE {where}",
            params,
        )
        ids = [q["id"] for q in questions]
        finished = [q for q in questions if q["error"] is None and q["total_ms"] is not None]
        times = sorted(q["total_ms"] for q in finished)

        def pct(values: list[int], p: float) -> int | None:
            return values[min(len(values) - 1, int(p * len(values)))] if values else None

        stop_reasons: dict[str, int] = {}
        for q in finished:
            reason = q["stop_reason"] or "unknown"
            stop_reasons[reason] = stop_reasons.get(reason, 0) + 1
        models: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        if ids:
            marks = ",".join("?" for _ in ids)
            models = store.query(
                "SELECT model, COUNT(*) AS calls, SUM(status != 'ok') AS errors, "
                f"AVG(ms) AS avg_ms FROM calls WHERE question_id IN ({marks}) "
                "GROUP BY model ORDER BY calls DESC LIMIT 10",
                ids,
            )
            decisions = store.query(
                "SELECT laya_status AS status, COUNT(*) AS n, "
                "SUM(laya_value IS NOT NULL AND laya_value = rules_value) AS agree "
                f"FROM decisions WHERE question_id IN ({marks}) GROUP BY laya_status",
                ids,
            )
        quota = []
        for m in engine.registry.all():
            provider = engine.registry.providers[m.provider]
            if provider.local or model_status(m, access) != "ready":
                continue
            left = engine.quota.left(m, access.key_id(m.provider))
            if left.rpd is None:
                continue
            quota.append(
                {
                    "model": m.id,
                    "rpd_left": left.rpd,
                    "free_rpd": m.free_rpd or left.rpd,
                    "own_key": m.provider in access.user_keys,
                }
            )
        quota.sort(key=lambda row: row["rpd_left"] / max(1, row["free_rpd"]))
        predicted = [d for d in decisions if d["status"] in ("ok", "late")]
        return {
            "hours": hours,
            "scope": "mine" if mine else "all",
            "questions": len(questions),
            "errors": sum(1 for q in questions if q["error"]),
            "passed": sum(1 for q in finished if q["stop_reason"] in ("passed", "cache")),
            "escalated": sum(1 for q in finished if (q["stages_used"] or 0) > 2),
            "avg_stages": round(sum(q["stages_used"] or 0 for q in finished) / len(finished), 2)
            if finished
            else None,
            "p50_ms": pct(times, 0.5),
            "p95_ms": pct(times, 0.95),
            "requests_used": sum(q["requests_used"] or 0 for q in finished),
            "cache_hits": sum(q["cache_hit"] or 0 for q in finished),
            "thumbs_up": sum(1 for q in questions if q["feedback"] == 1),
            "thumbs_down": sum(1 for q in questions if q["feedback"] == -1),
            "stop_reasons": stop_reasons,
            "models": [
                {
                    "model": m["model"],
                    "calls": m["calls"],
                    "errors": m["errors"] or 0,
                    "avg_ms": round(m["avg_ms"] or 0),
                }
                for m in models
            ],
            "laya": {
                "status": engine.laya.status if engine.laya is not None else "off",
                "decisions": sum(d["n"] for d in decisions),
                "predicted": sum(d["n"] for d in predicted),
                "agree_with_rules": sum(d["agree"] or 0 for d in predicted),
                "by_status": {d["status"]: d["n"] for d in decisions},
            },
            "quota": quota[:12],
        }

    @api.get("/models")
    async def models(access: AccessDep) -> dict[str, Any]:
        registry = engine.registry
        providers = [
            {
                "id": p.id,
                "label": p.label,
                "configured": registry.is_configured(p.id, access),
                "own_key": p.id in access.user_keys,
                "local": p.local,
                "env": p.key_env or p.base_env,
                "signup_url": p.signup_url,
                "free_tier": p.free_tier,
                "free_tier_note": p.free_tier_note,
            }
            for p in registry.providers.values()
        ]
        models = [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "family": m.family,
                "status": model_status(m, access),
                "context_window": m.context_window,
                "free_rpd": m.free_rpd,
                "local": registry.providers[m.provider].local,
                "reasoning": m.reasoning,
                "vision": m.vision,
                "strength": m.strength,
                "skills": {t: engine.skills.skill(m, t) for t in m.skills},
                "measured": {
                    t: {k: d[k] for k in ("eval_n", "live_n")}
                    for t in m.skills
                    if (d := engine.skills.detail(m, t))["eval_n"] or d["live_n"]
                },
                "source": m.source,
            }
            for m in registry.all()
        ]
        health = {p: s.as_dict() for p, s in (engine.sync.status if engine.sync else {}).items()}
        for provider in providers:
            provider["health"] = health.get(provider["id"])
        return {"providers": providers, "models": models}

    app.include_router(v1)
    app.include_router(api)
    return app
