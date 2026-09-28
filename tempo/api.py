"""HTTP API: the web app, an OpenAI-compatible endpoint, and a trace-event stream."""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from tempo import __version__
from tempo.config import Settings
from tempo.engine import DEFAULT_SYSTEM_PROMPT, Engine
from tempo.events import STREAM_EVENTS
from tempo.types import MODES, ModelInfo

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


class TempoOptions(BaseModel):
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


class AskRequest(BaseModel):
    prompt: str | None = None
    messages: list[dict[str, Any]] | None = None
    mode: str = "auto"
    model: str | None = None
    privacy: Literal["default", "local_only"] = "default"
    allow_providers: list[str] | None = None


def _sse(payload: Any) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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

    def require_key(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_key:
            return
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), settings.api_key.encode()):
            raise APIError(401, "Invalid or missing API key.", code="invalid_api_key")

    def model_status(model: ModelInfo) -> str:
        if not engine.registry.is_configured(model.provider):
            return "not configured"
        if model.installed is False:
            return "not installed"
        return engine.health.unavailable_reason(model) or "ready"

    def ready_models() -> list[ModelInfo]:
        return [m for m in engine.registry.all() if model_status(m) == "ready"]

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
    async def list_models() -> dict[str, Any]:
        virtual = [
            {"id": f"tempo/{mode}", "object": "model", "created": 0, "owned_by": "tempo"}
            for mode in MODES
        ]
        concrete = [
            {"id": m.id, "object": "model", "created": 0, "owned_by": m.provider}
            for m in ready_models()
        ]
        return {"object": "list", "data": virtual + concrete}

    @v1.post("/chat/completions", response_model=None)
    async def chat_completions(req: ChatCompletionRequest) -> JSONResponse | StreamingResponse:
        mode, explicit = resolve_model(req.model)
        mode = req.tempo.mode if req.tempo.mode in MODES else mode
        options = engine.options(
            mode=mode,
            model=explicit,
            allow_providers=req.tempo.allow_providers,
            local_only=req.tempo.privacy == "local_only",
            temperature=req.temperature,
            max_tokens=req.max_completion_tokens or req.max_tokens,
            restart_on_partial_failure=not req.stream,
        )
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
                "attempts": result.attempts,
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
                if event.type == "route":
                    label = event.data["model"]
                elif event.type == "fallback":
                    label = event.data["to"]

                if event.type in STREAM_EVENTS:
                    if not role_sent:
                        yield _sse(chunk({"role": "assistant", "content": ""}))
                        role_sent = True
                    field = "content" if event.type == "answer_delta" else "reasoning_content"
                    yield _sse(chunk({field: event.data["delta"]}))
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
    async def ask(req: AskRequest) -> StreamingResponse:
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
        )

        async def stream() -> AsyncIterator[str]:
            async for event in engine.run(messages, options):
                yield _sse(event.to_dict())

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @api.get("/models")
    async def models() -> dict[str, Any]:
        registry = engine.registry
        providers = [
            {
                "id": p.id,
                "label": p.label,
                "configured": registry.is_configured(p.id),
                "local": p.local,
                "env": p.key_env or p.base_env,
                "signup_url": p.signup_url,
            }
            for p in registry.providers.values()
        ]
        models = [
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "family": m.family,
                "status": model_status(m),
                "context_window": m.context_window,
                "free_rpd": m.free_rpd,
                "local": registry.providers[m.provider].local,
                "reasoning": m.reasoning,
                "vision": m.vision,
                "strength": m.strength,
                "skills": m.skills,
            }
            for m in registry.all()
        ]
        return {"providers": providers, "models": models}

    app.include_router(v1)
    app.include_router(api)
    return app
