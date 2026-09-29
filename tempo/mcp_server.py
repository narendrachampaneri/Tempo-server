"""Tempo-server as an MCP server, so any AI assistant can use it (docs/MCP.md).

- ``tempo-server mcp``: stdio, for desktop apps (Claude Desktop, Claude Code, Cursor, VS Code).
  The caller is the local owner: the keys `tempo-server setup` stored, and local models.
- ``tempo-server mcp --http``: Streamable HTTP at ``/mcp``. Every request needs
  ``Authorization: Bearer <Tempo key>`` (a user's key from `tempo-server users add`, or
  TEMPO_API_KEY); each caller uses their own provider keys, like the OpenAI-compatible API.

Tools: ask, second_opinion, verify, models, quota (tempo/assist.py does the work).
Built on the official MCP Python SDK (MIT).
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from tempo import __version__, assist
from tempo.accounts import ADMIN_USER, LOCAL_USER
from tempo.types import Access

if TYPE_CHECKING:
    from tempo.engine import Engine

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Tempo-server routes each question to the best free or open model available to this user, "
    "checks the answer with a judge from another model family, and fixes weak answers. Use "
    "`ask` for a checked answer, `second_opinion` to compare two model families, `verify` to "
    "check an answer you already have, `models` for the free models and their health, and "
    "`quota` for the free requests left today."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ASKS_MODELS = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

Scope = MutableMapping[str, Any]
ASGIApp = Callable[[Scope, Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Any]


class Unauthorized(Exception):
    pass


def caller_id(engine: Engine, admin_key: str | None, authorization: str | None) -> str | None:
    """The Tempo user a bearer token belongs to (TEMPO_API_KEY is the admin), or None."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        return None
    if admin_key and hmac.compare_digest(token.encode(), admin_key.encode()):
        return ADMIN_USER
    user = engine.accounts.authenticate(token)
    return user.id if user is not None else None


def build_server(engine: Engine, admin_key: str | None = None) -> MCPServer:
    server = MCPServer(
        name="tempo-server",
        title="Tempo-server",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/narendrachampaneri/Tempo-server",
    )

    def access(ctx: Context | None) -> Access:
        """stdio: the local owner. HTTP: whoever the bearer token belongs to."""
        request = None
        if ctx is not None:
            try:
                request = ctx.request_context.request
            except ValueError:
                request = None
        if request is None or not hasattr(request, "headers"):
            return engine.access_for(LOCAL_USER)
        user_id = caller_id(engine, admin_key, request.headers.get("authorization"))
        if user_id is None:  # the HTTP middleware already refused it; never fall back
            raise Unauthorized("Invalid or missing Tempo API key.")
        return engine.access_for(user_id)

    @server.tool(
        title="Ask Tempo-server",
        description="Answer a question with the best free or open model available, checked by "
        "a judge from another model family and fixed when weak. Returns the answer, the models "
        "used and the check results. mode: auto (balanced), fast, or best. private: never use a "
        "model whose free tier may log or train on prompts. local_only: only local models "
        "(Ollama); nothing leaves this computer.",
        annotations=ASKS_MODELS,
    )
    async def ask(
        question: str,
        mode: Literal["auto", "fast", "best"] = "auto",
        private: bool = False,
        local_only: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await assist.ask(
            engine, question, mode=mode, private=private, local_only=local_only, access=access(ctx)
        )

    @server.tool(
        title="Second opinion",
        description="Ask two different model families the same question and show where their "
        "answers agree and where they differ (compared by a third family when one is "
        "available).",
        annotations=ASKS_MODELS,
    )
    async def second_opinion(
        question: str, private: bool = False, ctx: Context | None = None
    ) -> dict[str, Any]:
        return await assist.second_opinion(engine, question, private=private, access=access(ctx))

    @server.tool(
        title="Verify an answer",
        description="Check an answer to a question with Tempo's quick checks and a judge from "
        "a different model family than the one that wrote it. Returns the verdict (pass or "
        "fail), a score from 0 to 10 and the problems found. answer_model: the model id or "
        "family that wrote the answer, so the judge comes from another family.",
        annotations=ASKS_MODELS,
    )
    async def verify(
        question: str,
        answer: str,
        answer_model: str | None = None,
        private: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await assist.verify(
            engine,
            question,
            answer,
            answer_model=answer_model,
            private=private,
            access=access(ctx),
        )

    @server.tool(
        title="Free models",
        description="The live list of free and open models Tempo-server knows, with limits, "
        "data policy, health, and whether each is ready for you.",
        annotations=READ_ONLY,
    )
    async def models(include_all: bool = False, ctx: Context | None = None) -> dict[str, Any]:
        return {"models": assist.models(engine, access(ctx), include_all=include_all)}

    @server.tool(
        title="Free quota",
        description="Free requests left today per provider, when they reset, and whether every "
        "free quota is used up (then local models answer).",
        annotations=READ_ONLY,
    )
    async def quota(ctx: Context | None = None) -> dict[str, Any]:
        return assist.quota(engine, access(ctx))

    return server


def require_key(app: ASGIApp, engine: Engine, admin_key: str | None) -> ASGIApp:
    """Refuse every HTTP request without a valid Tempo key (401), before MCP sees it."""

    async def guarded(scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            if caller_id(engine, admin_key, headers.get("authorization")) is None:
                body = json.dumps(
                    {"error": "Invalid or missing Tempo API key (Authorization: Bearer <key>)."}
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b'Bearer realm="tempo-server"'),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await app(scope, receive, send)

    return guarded


def http_app(engine: Engine, admin_key: str | None, host: str = "127.0.0.1") -> ASGIApp:
    server = build_server(engine, admin_key)
    return require_key(server.streamable_http_app(host=host), engine, admin_key)
