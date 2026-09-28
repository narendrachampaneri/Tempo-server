"""The ``tempo`` command: ask questions, chat, list models, run the server.

Trace lines go to stderr and the answer to stdout, so ``tempo ask "..." > answer.md`` works.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tempo.config import Settings
from tempo.engine import DEFAULT_SYSTEM_PROMPT, Engine, RunOptions, RunResult, collect
from tempo.types import MODES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Tempo: ask once, and Tempo picks the best available free or open-source model.",
)
err = Console(stderr=True, highlight=False)
out = Console(highlight=False)

ModeOption = Annotated[str, typer.Option("--mode", "-m", help=f"Routing mode: {', '.join(MODES)}.")]
PrivateOption = Annotated[bool, typer.Option("--private", help="Only use local models (Ollama).")]
TraceOption = Annotated[
    bool, typer.Option("--trace/--no-trace", help="Show the thinking window on stderr.")
]


def _engine() -> Engine:
    return Engine.from_settings(Settings.from_env())


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise typer.BadParameter(f"mode must be one of: {', '.join(MODES)}")
    return mode


def _trace(text: str, style: str = "dim") -> None:
    err.print(f"▸ {text}", style=style, markup=False)


async def _stream_answer(
    engine: Engine, messages: list[dict[str, Any]], options: RunOptions, show_trace: bool
) -> RunResult:
    result = RunResult()
    reasoning_noted = False
    answer_open = False

    async for event in engine.run(messages, options):
        result.events.append(event)
        data = event.data
        if event.type == "answer_delta":
            if not answer_open and show_trace:
                err.rule(style="dim")
            answer_open = True
            result.text += data["delta"]
            sys.stdout.write(data["delta"])
            sys.stdout.flush()
            continue
        if event.type == "reasoning_delta":
            result.reasoning += data["delta"]
            if show_trace and not reasoning_noted:
                _trace(f"{data['model']} is reasoning…")
                reasoning_noted = True
            continue
        if event.type == "call_start":
            reasoning_noted = False
            result.attempts = data["attempt"]
        if event.type == "answer_reset":
            result.text = ""
            if answer_open:
                sys.stdout.write("\n")
                answer_open = False
        if event.type == "done":
            result.model = data["model"]
        if event.type == "error":
            result.error = data["message"]
            result.error_kind = data.get("kind")

        text = event.to_dict().get("text")
        if not text:
            continue
        if event.type == "error":
            if answer_open:
                sys.stdout.write("\n")
                answer_open = False
            _trace(text, style="bold red")
        elif show_trace:
            if answer_open:
                sys.stdout.write("\n")
                sys.stdout.flush()
                answer_open = False
            _trace(text, style="yellow" if event.type in ("call_error", "fallback") else "dim")

    if answer_open or (result.text and not result.text.endswith("\n")):
        sys.stdout.write("\n")
        sys.stdout.flush()
    return result


@app.command()
def ask(
    question: Annotated[
        list[str] | None, typer.Argument(help="Your question. Use '-' or omit to read stdin.")
    ] = None,
    mode: ModeOption = "auto",
    model: Annotated[
        str | None, typer.Option("--model", help="Try this registry model first.")
    ] = None,
    provider: Annotated[
        list[str] | None,
        typer.Option("--provider", "-p", help="Only use these providers (repeatable)."),
    ] = None,
    private: PrivateOption = False,
    trace: TraceOption = True,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print one JSON object instead of streaming.")
    ] = False,
) -> None:
    """Ask one question."""
    text = " ".join(question or []).strip()
    if not text or text == "-":
        text = sys.stdin.read().strip()
    if not text:
        raise typer.BadParameter("no question given")

    engine = _engine()
    options = engine.options(
        mode=_check_mode(mode),
        model=model,
        allow_providers=provider or None,
        local_only=private,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
    )
    messages = [{"role": "user", "content": text}]

    async def main() -> RunResult:
        await engine.startup()
        if as_json:
            return await collect(engine.run(messages, options))
        return await _stream_answer(engine, messages, options, trace)

    result = asyncio.run(main())
    if as_json:
        payload = {
            "answer": result.text,
            "model": result.model,
            "attempts": result.attempts,
            "error": result.error,
            "trace": result.trace,
        }
        out.print_json(json.dumps(payload, ensure_ascii=False))
    if result.error:
        raise typer.Exit(1)


@app.command()
def chat(mode: ModeOption = "auto", private: PrivateOption = False, trace: TraceOption = True):
    """Interactive chat. Commands: /mode <name>, /clear, /exit."""
    engine = _engine()
    current_mode = _check_mode(mode)
    history: list[dict[str, Any]] = []
    err.print(f"Tempo chat · mode {current_mode} · type /exit to quit", style="bold", markup=False)

    async def main() -> None:
        nonlocal current_mode, history
        await engine.startup()
        while True:
            try:
                line = await asyncio.to_thread(input, "\nyou › ")
            except (EOFError, KeyboardInterrupt):
                err.print()
                return
            line = line.strip()
            if not line:
                continue
            if line in ("/exit", "/quit"):
                return
            if line == "/clear":
                history = []
                err.print("History cleared.", style="dim")
                continue
            if line.startswith("/mode"):
                parts = line.split()
                if len(parts) == 2 and parts[1] in MODES:
                    current_mode = parts[1]
                    err.print(f"Mode: {current_mode}", style="dim", markup=False)
                else:
                    err.print(f"Usage: /mode {{{'|'.join(MODES)}}}", style="red", markup=False)
                continue

            history.append({"role": "user", "content": line})
            options = engine.options(
                mode=current_mode, local_only=private, system_prompt=DEFAULT_SYSTEM_PROMPT
            )
            out.print("tempo ›", style="bold cyan")
            result = await _stream_answer(engine, history, options, trace)
            if result.error:
                history.pop()
            else:
                history.append({"role": "assistant", "content": result.text})

    asyncio.run(main())


@app.command()
def models() -> None:
    """List models and whether each one is ready to use."""
    engine = _engine()
    asyncio.run(engine.startup())
    registry = engine.registry

    table = Table(title="Tempo models", title_style="bold", header_style="bold")
    for column in ("status", "model", "provider", "free / day", "context", "strength"):
        table.add_column(column)
    for m in sorted(registry.all(), key=lambda m: (m.provider, m.id)):
        if not registry.is_configured(m.provider):
            status = "[dim]not configured[/dim]"
        elif m.installed is False:
            status = "[dim]not installed[/dim]"
        else:
            reason = engine.health.unavailable_reason(m)
            status = f"[yellow]{reason}[/yellow]" if reason else "[green]ready[/green]"
        provider = registry.providers[m.provider]
        free = "local" if provider.local else (f"{m.free_rpd:,}" if m.free_rpd else "-")
        table.add_row(
            status, m.id, provider.label, free, f"{m.context_window:,}", f"{m.strength:.2f}"
        )
    out.print(table)

    missing = [p for p in registry.providers.values() if not registry.is_configured(p.id)]
    if missing:
        out.print("\nTo enable more providers, set these (free) in your environment or .env:")
        for p in missing:
            env = p.key_env or p.base_env
            out.print(f"  {env:<20} {p.label:<26} {p.signup_url or ''}", markup=False)


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Run the web app and API server."""
    import uvicorn

    from tempo.api import create_app

    err.print(f"Tempo on http://{host}:{port}  (API: /v1, docs: /docs)", markup=False)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
