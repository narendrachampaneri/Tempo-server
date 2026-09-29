"""The ``tempo`` command: ask questions, chat, list models, run the server.

Trace lines go to stderr and the answer to stdout, so ``tempo-server ask "..." > answer.md`` works.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from tempo import paths
from tempo.config import Settings
from tempo.engine import (
    DEFAULT_SYSTEM_PROMPT,
    NO_MODEL_LINE,
    Engine,
    RunOptions,
    RunResult,
    collect,
)
from tempo.types import MODES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Tempo-server: ask once, and Tempo picks the best available free or open-source model.",
)
err = Console(stderr=True, highlight=False)


def _version(value: bool) -> None:
    if value:
        from tempo import __version__

        print(f"tempo-server {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Tempo-server: ask once, and Tempo picks the best available free or open-source model."""


out = Console(highlight=False)

ModeOption = Annotated[str, typer.Option("--mode", "-m", help=f"Routing mode: {', '.join(MODES)}.")]
PrivateOption = Annotated[bool, typer.Option("--private", help="Only use local models (Ollama).")]
NoLoggingOption = Annotated[
    bool,
    typer.Option(
        "--no-logging",
        help="Never use models whose free tier may log or train on prompts (tempo-server terms).",
    ),
]
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
    """Stream the answer to stdout and the thinking window to stderr.

    The first good answer streams at once; if the background check finds a real problem, the
    revised answer is printed below it with what was found. If the final answer differs from
    what was shown last, it is printed once more at the end.
    """
    result = RunResult()
    shown = ""  # text of the answer currently on screen
    reasoning_noted = False
    answer_open = False

    def close_answer() -> None:
        nonlocal answer_open
        if answer_open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            answer_open = False

    async for event in engine.run(messages, options):
        result.apply(event)
        data = event.data
        if event.type == "answer_delta":
            if not answer_open and show_trace:
                err.rule(style="dim")
            answer_open = True
            shown += data["delta"]
            sys.stdout.write(data["delta"])
            sys.stdout.flush()
            continue
        if event.type == "reasoning_delta":
            if show_trace and not reasoning_noted:
                _trace(f"{data['model']} is reasoning…")
                reasoning_noted = True
            continue
        if event.type == "call_start":
            reasoning_noted = False
        if event.type == "answer_reset":
            shown = ""
            close_answer()
        if event.type == "answer_revised" and data["answer"].strip() != shown.strip():
            close_answer()
            found = "; ".join(data.get("issues") or []) or "a better answer"
            err.rule(f"Revised after the check: {found}", style="yellow")
            sys.stdout.write(data["answer"].rstrip() + "\n")
            sys.stdout.flush()
            shown = data["answer"]
            continue
        if event.type == "answer_final" and data["answer"].strip() != shown.strip():
            close_answer()
            if show_trace:
                err.rule(style="dim")
            sys.stdout.write(data["answer"].rstrip() + "\n")
            sys.stdout.flush()
            shown = data["answer"]
        if event.type == "answer_final" and data.get("note"):
            close_answer()
            _trace(data["note"], style="yellow")

        text = event.to_dict().get("text")
        if not text:
            continue
        if event.type == "error":
            close_answer()
            _trace(text, style="bold red")
        elif show_trace:
            close_answer()
            warn = event.type in ("call_error", "fallback", "budget")
            _trace(text, style="yellow" if warn else "dim")

    close_answer()
    return result


def _stage_options(
    max_stages: int | None,
    time_budget: float | None,
    quota_budget: int | None,
    strategy: str | None,
) -> dict[str, Any]:
    if strategy is not None and strategy not in ("single", "cascade", "mixture", "decompose"):
        raise typer.BadParameter("strategy must be single, cascade, mixture or decompose")
    return {
        "max_stages": max_stages,
        "time_budget_s": time_budget,
        "quota_budget": quota_budget,
        "strategy": strategy,
    }


StagesOption = Annotated[
    int | None, typer.Option("--max-stages", "-s", help="Most stages for this question.")
]
TimeOption = Annotated[
    float | None, typer.Option("--time-budget", help="Seconds allowed for this question.")
]
QuotaOption = Annotated[
    int | None, typer.Option("--quota-budget", help="Most free provider requests to spend.")
]
StrategyOption = Annotated[
    str | None,
    typer.Option("--strategy", help="Force single, cascade, mixture or decompose."),
]


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
    no_logging: NoLoggingOption = False,
    trace: TraceOption = True,
    max_stages: StagesOption = None,
    time_budget: TimeOption = None,
    quota_budget: QuotaOption = None,
    strategy: StrategyOption = None,
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
        no_logging=no_logging,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        access=engine.access_for("local"),
        **_stage_options(max_stages, time_budget, quota_budget, strategy),
    )
    messages = [{"role": "user", "content": text}]

    async def main() -> RunResult:
        await engine.startup(oneshot=True)
        if as_json:
            return await collect(engine.run(messages, options))
        return await _stream_answer(engine, messages, options, trace)

    result = asyncio.run(main())
    if as_json:
        payload = {
            "answer": result.text,
            "model": result.model,
            "question_id": result.question_id,
            "stages": result.stages,
            "attempts": result.attempts,
            "requests": result.requests,
            "score": result.score,
            "stop_reason": result.stop_reason,
            "error": result.error,
            "trace": result.trace,
        }
        out.print_json(json.dumps(payload, ensure_ascii=False))
    if result.error:
        raise typer.Exit(1)


@app.command()
def chat(
    mode: ModeOption = "auto",
    private: PrivateOption = False,
    no_logging: NoLoggingOption = False,
    trace: TraceOption = True,
    max_stages: StagesOption = None,
):
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
                mode=current_mode,
                local_only=private,
                no_logging=no_logging,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                max_stages=max_stages,
                access=engine.access_for("local"),
            )
            out.print("tempo ›", style="bold cyan")
            result = await _stream_answer(engine, history, options, trace)
            if result.error:
                history.pop()
            else:
                history.append({"role": "assistant", "content": result.text})

    asyncio.run(main())


def _free_catalog(engine: Engine, as_json: bool, everything: bool) -> None:
    from tempo import catalog
    from tempo.sync import RegistrySync

    status = asyncio.run(RegistrySync(engine.registry, engine.health).run(engine.listing_keys()))
    engine.save_catalog(status)
    checked = {p: s.checked_at for p, s in status.items()}
    found = catalog.rows(engine.registry, engine.health, checked, everything)
    if as_json:
        out.print_json(
            data={"providers": {p: s.as_dict() for p, s in status.items()}, "models": found}
        )
        return
    table = Table(title="Free models (live)", title_style="bold", header_style="bold")
    columns = (
        "provider",
        "model",
        "type",
        "context",
        "max out",
        "inputs",
        "tools",
        "limits",
        "data policy",
        "health",
        "checked",
        "status",
    )
    for column in columns:
        table.add_column(column, overflow="fold")
    for r in found:
        policy = f"[red]{r['data_policy']}[/red]" if r["flagged"] else r["data_policy"]
        name = r["model"].removeprefix(r["provider"] + "/")
        tags = [t for t in ("preview" if r["preview"] else "", r["domain"] or "") if t]
        table.add_row(
            r["provider"],
            name + (f" [dim]({', '.join(tags)})[/dim]" if tags else ""),
            r["type"],
            f"{r['context']:,}" if r["context"] else "?",
            f"{r['max_output']:,}" if r["max_output"] else "-",
            ",".join(r["inputs"]),
            {True: "yes", False: "no", None: "-"}[r["tools"]],
            r["limits"],
            policy,
            r["health"],
            catalog.when_text(r["last_check"]),
            r["status"],
        )
    out.print(table)
    for p, s in sorted(status.items()):
        state = "[green]ok[/green]" if s.ok else f"[red]{s.error}[/red]"
        out.print(f"{p}: {state} · {s.listed} listed", highlight=False)
    counts: dict[str, int] = {}
    for r in found:
        if r["type"] in ("chat", "code", "vision"):
            counts[r["provider"]] = counts.get(r["provider"], 0) + 1
    summary = ", ".join(f"{p} {n}" for p, n in sorted(counts.items()))
    out.print(f"Chat-capable free models: {sum(counts.values())} ({summary})", markup=False)


models_app = typer.Typer(
    help="List models (--free: the live free catalog); import, compare and promote Tempo's own "
    "trained models.",
)
app.add_typer(models_app, name="models")


@models_app.callback(invoke_without_command=True)
def models(
    ctx: typer.Context,
    free: Annotated[
        bool,
        typer.Option("--free", help="Live free-model catalog: read every provider's list now."),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="With --free: print JSON.")] = False,
    everything: Annotated[
        bool, typer.Option("--all", help="With --free: include expired and delisted models.")
    ] = False,
) -> None:
    """List models and whether each one is ready to use."""
    if ctx.invoked_subcommand:
        return
    engine = _engine()
    asyncio.run(engine.startup(oneshot=True))
    if free:
        _free_catalog(engine, as_json, everything)
        return
    registry = engine.registry

    table = Table(title="Tempo models", title_style="bold", header_style="bold")
    for column in ("status", "model", "provider", "free / day", "context", "strength"):
        table.add_column(column)
    for m in sorted(registry.all(), key=lambda m: (m.provider, m.id)):
        if not m.chat_capable:
            continue  # speech, safety, embedding...: `tempo-server models --free` lists them
        if not registry.is_enabled(m.provider):
            status = "[dim]off by default[/dim]"
        elif not registry.is_configured(m.provider):
            status = "[dim]not configured[/dim]"
        elif m.installed is False:
            status = "[dim]not installed[/dim]"
        else:
            reason = engine.health.unavailable_reason(m)
            status = f"[yellow]{reason}[/yellow]" if reason else "[green]ready[/green]"
        provider = registry.providers[m.provider]
        if provider.local:
            free = "local"
        elif m.free_rpd:
            free = f"{m.free_rpd:,}"
        elif m.free_tpd:
            free = f"{m.free_tpd // 1000:,}K tokens"
        else:
            free = "-"
        if provider.free_tier == "trial" and not provider.local:
            free += " (trial)"
        table.add_row(
            status, m.id, provider.label, free, f"{m.context_window:,}", f"{m.strength:.2f}"
        )
    out.print(table)
    for p in registry.providers.values():
        if p.free_tier == "trial" and p.free_tier_note:
            out.print(f"{p.label}: {p.free_tier_note}", style="yellow", markup=False)

    missing = [
        p
        for p in registry.providers.values()
        if not registry.is_configured(p.id) and p.enabled and not p.byok_only
    ]
    if missing:
        out.print("\nTo enable more providers, set these (free) in your environment or .env:")
        for p in missing:
            env = p.key_env or p.base_env
            out.print(f"  {env:<20} {p.label:<26} {p.signup_url or ''}", markup=False)
    for p in registry.providers.values():
        if not p.enabled and p.disabled_note:
            out.print(f"{p.label}: {p.disabled_note}", style="dim", markup=False)


def _loop_error(exc: Exception) -> typer.Exit:
    err.print(str(exc), style="red", markup=False, soft_wrap=True)
    return typer.Exit(1)


@models_app.command("import")
def models_import(
    path: Annotated[
        Path,
        typer.Argument(help="A notebook's output: the downloaded zip, its folder, or a .gguf."),
    ],
    ollama: Annotated[
        bool, typer.Option("--ollama/--no-ollama", help="Register Tempo-Core with Ollama.")
    ] = True,
) -> None:
    """Load a trained Laya checkpoint, or register a Tempo-Core GGUF with Ollama."""
    from tempo import model_loop

    engine = _engine()
    try:
        results = model_loop.import_model(engine, path, ollama=ollama)
    except model_loop.LoopError as exc:
        raise _loop_error(exc) from exc
    for item in results:
        what = "Tempo-Core" if item.kind == model_loop.CORE else "Laya (Tempo-Router/Judge)"
        extra = " (dry run)" if item.report.get("dry_run") else ""
        err.print(
            f"Imported {what} {item.version}{extra} to {item.path}", markup=False, soft_wrap=True
        )
        for note in item.notes:
            err.print(f"  {note}", markup=False, soft_wrap=True)
        heldout = item.report.get("heldout") or {}
        shown = {k: v for k, v in heldout.items() if not isinstance(v, dict) and v is not None}
        if shown:
            err.print(f"  notebook's held-out scores: {shown}", markup=False, soft_wrap=True)
    err.print("Next: tempo-server models compare", markup=False)


@models_app.command("compare")
def models_compare(
    kind: Annotated[
        str, typer.Option("--kind", "-k", help="tempo-core or laya (default: the last imported).")
    ] = "",
    old: Annotated[
        str,
        typer.Option(
            "--old",
            help="tempo-core: the promoted version, 'base', a version, ollama:NAME or "
            "gguf:PATH. laya: 'current' (TEMPO_LAYA_MODEL or stock), 'stock', a version or a "
            "folder.",
        ),
    ] = "",
    new: Annotated[str, typer.Option("--new", help="Default: the last imported version.")] = (
        "latest"
    ),
    data: Annotated[
        Path | None,
        typer.Option("--data", help="Pack folder with the held-out set (default: the latest)."),
    ] = None,
    min_questions: Annotated[
        int | None,
        typer.Option(
            "--min-questions",
            help="Held-out questions needed per task type (30) or rows per Laya decision (50).",
        ),
    ] = None,
    max_tokens: Annotated[int, typer.Option("--max-tokens", help="Per answer.")] = 512,
    limit: Annotated[int | None, typer.Option("--limit", help="Only this many questions.")] = None,
    min_speed: Annotated[
        float, typer.Option("--min-speed", help="Tokens a second the new version must reach.")
    ] = 8.0,
    llama_server: Annotated[
        Path | None, typer.Option("--llama-server", help="llama.cpp's server, for gguf:PATH.")
    ] = None,
) -> None:
    """Run the held-out set on old and new, and apply the promotion gate per task type."""
    from tempo import model_loop, training

    engine = _engine()
    asyncio.run(engine.startup(oneshot=True))
    pack = data or training.latest_pack(engine)
    try:
        if not kind:
            items = model_loop.book(engine)["imports"]
            if not items:
                raise model_loop.LoopError("Nothing imported yet (tempo-server models import).")
            kind = items[-1]["kind"]
        if kind == model_loop.CORE:
            result = model_loop.compare_core(
                engine,
                pack,
                old=old,
                new=new,
                min_questions=min_questions or model_loop.MIN_CORE_QUESTIONS,
                min_speed=min_speed,
                max_tokens=max_tokens,
                limit=limit,
                llama_server=llama_server,
                say=lambda text: err.print(text, markup=False, soft_wrap=True),
            )
        elif kind == model_loop.LAYA:
            result = model_loop.compare_laya(
                engine,
                pack,
                old=old or "current",
                new=new,
                min_rows=min_questions or model_loop.MIN_LAYA_ROWS,
                say=lambda text: err.print(text, markup=False, soft_wrap=True),
            )
        else:
            raise model_loop.LoopError("--kind is tempo-core or laya")
    except model_loop.LoopError as exc:
        raise _loop_error(exc) from exc
    if kind == model_loop.CORE:
        table = Table(
            title=f"{result['labels']['old']} vs {result['labels']['new']}", header_style="bold"
        )
        for column in ("task type", "questions", "old", "new", "wins", "losses", "verdict"):
            table.add_column(column)
        for t in result["types"]:
            verdict = (
                f"[green]new[/green]: {t['reason']}" if t["takes_over"] else (f"old: {t['reason']}")
            )
            table.add_row(
                t["task"],
                str(t["n"]),
                f"{t['old']:.3f}",
                f"{t['new']:.3f}",
                str(t["wins"]),
                str(t["losses"]),
                verdict,
            )
    else:
        table = Table(title="Laya: old vs new checkpoint", header_style="bold")
        for column in ("decision", "rows", "old", "new", "verdict"):
            table.add_column(column)
        for d in result["decisions"]:
            table.add_row(
                d["decision"], str(d["n"]), f"{d['old']:.3f}", f"{d['new']:.3f}", d["reason"]
            )
    out.print(table)
    for reason in result["reasons"]:
        err.print(f"  {reason}", markup=False, soft_wrap=True)
    if result["release"]:
        which = ", ".join(result.get("promote") or []) or "all decisions"
        err.print(
            f"Gate passed ({which}). Next: tempo-server models promote --kind {kind}",
            style="green",
            markup=False,
            soft_wrap=True,
        )
    else:
        err.print("Gate not passed: the old version stays.", style="yellow", markup=False)


@models_app.command("promote")
def models_promote(
    kind: Annotated[
        str, typer.Option("--kind", "-k", help="tempo-core or laya (default: the last compared).")
    ] = "",
) -> None:
    """Apply the last comparison: the new version takes the task types it won."""
    from tempo import model_loop

    engine = _engine()
    try:
        if not kind:
            compared = model_loop.book(engine)["compare"]
            if not compared:
                raise model_loop.LoopError("Run `tempo-server models compare` first.")
            kind = max(compared.values(), key=lambda r: r["at"])["kind"]
        change = model_loop.promote(engine, kind)
    except model_loop.LoopError as exc:
        raise _loop_error(exc) from exc
    if not change["changed"]:
        err.print(
            "Not promoted: the last comparison did not pass the gate.", style="yellow", markup=False
        )
        for reason in change["reasons"]:
            err.print(f"  {reason}", markup=False, soft_wrap=True)
        raise typer.Exit(1)
    if kind == model_loop.CORE:
        err.print(
            f"Tempo-Core now answers: {', '.join(change['tasks'])}", style="green", markup=False
        )
        for task, name in sorted(change["routes"].items()):
            err.print(f"  {task}: {name}", markup=False)
    else:
        err.print(
            f"TEMPO_LAYA_MODEL={change['laya_model']} (in settings.env). It starts in "
            "shadow mode; TEMPO_LAYA_TAKEOVER hands it decisions it wins "
            "(docs/LAYA_TUNING.md).",
            style="green",
            markup=False,
            soft_wrap=True,
        )


@app.command(name="eval")
def eval_models(
    model: Annotated[
        list[str] | None, typer.Option("--model", help="Model id to measure (repeatable).")
    ] = None,
    task: Annotated[
        list[str] | None, typer.Option("--task", help="Only these tasks (repeatable).")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Questions per task (default: all).")
    ] = None,
) -> None:
    """Measure models on the probe set; the router then uses the measured skills."""
    from tempo.evals import EvalResult, run_evals

    engine = _engine()
    asyncio.run(engine.startup(oneshot=True))
    if model:
        chosen = [engine.registry.get(m) for m in model]
        missing = [m for m, c in zip(model, chosen, strict=True) if c is None]
        if missing:
            raise typer.BadParameter(f"unknown model(s): {', '.join(missing)}")
        targets = [c for c in chosen if c is not None]
    else:
        targets = [
            m
            for m in engine.registry.all()
            if engine.registry.is_configured(m.provider) and m.installed is not False
        ]
    if not targets:
        err.print("No configured models to measure.", style="red")
        raise typer.Exit(1)
    err.print(f"Measuring {len(targets)} model(s)…", style="dim", markup=False)

    def report(r: EvalResult) -> None:
        note = f" ({r.errors} failed)" if r.errors else ""
        err.print(f"  {r.model:<50} {r.task:<10} {r.score:.2f} on {r.n}{note}", markup=False)

    results = asyncio.run(run_evals(engine, targets, task, limit, on_result=report))
    table = Table(title="Measured skills (blended with priors)", header_style="bold")
    table.add_column("model")
    tasks = sorted({r.task for r in results})
    for t in tasks:
        table.add_column(t, justify="right")
    for m in targets:
        table.add_row(m.id, *(f"{engine.skills.skill(m, t):.2f}" for t in tasks))
    out.print(table)


@app.command()
def sync() -> None:
    """Refresh provider model lists now and report each provider's health."""
    from tempo.sync import RegistrySync

    engine = _engine()
    status = asyncio.run(RegistrySync(engine.registry, engine.health).run(engine.listing_keys()))
    engine.save_catalog(status)
    table = Table(title="Provider sync", header_style="bold")
    for column in ("provider", "status", "models listed", "new", "no longer listed"):
        table.add_column(column)
    for s in status.values():
        state = "[green]ok[/green]" if s.ok else f"[red]{s.error}[/red]"
        table.add_row(s.provider, state, str(s.listed), str(len(s.added)), str(len(s.removed)))
    out.print(table)
    if not status:
        err.print(NO_MODEL_LINE, style="dim", markup=False)


@app.command()
def setup(
    only: Annotated[
        str | None, typer.Option("--only", help="Providers to set up, e.g. groq,gemini.")
    ] = None,
    no_sync: Annotated[
        bool, typer.Option("--no-sync", help="Don't read the live model lists at the end.")
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="Ask nothing and store nothing: report keys found and the free capacity.",
        ),
    ] = False,
) -> None:
    """Set up free providers: keys, free limits and terms; shows your free requests a day."""
    from tempo.setup import Wizard

    names = {n.strip().lower() for n in only.split(",") if n.strip()} if only else None
    Wizard(_engine(), out, sync=not no_sync, interactive=not non_interactive).run(names)


@app.command()
def doctor(
    port: Annotated[int, typer.Option(help="The port `tempo-server serve` will use.")] = 8000,
    offline: Annotated[bool, typer.Option("--offline", help="Skip the network checks.")] = False,
) -> None:
    """Check the install (Python, data folder, keys, network, Ollama, port) and how to fix it."""
    from tempo import doctor as doc

    engine = _engine()
    checks = asyncio.run(doc.run_checks(engine, port=port, offline=offline))
    for check in checks:
        mark, style = doc.MARKS[check.status]
        out.print(f"{mark} {check.area}: {check.message}", style=style, markup=False)
        if check.fix and check.status != "ok":
            out.print(f"    fix: {check.fix}", markup=False)
    failed = [c for c in checks if c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]
    if failed:
        err.print(f"{len(failed)} problem(s) to fix.", style="red")
        raise typer.Exit(1)
    err.print("All good." if not warned else f"Works; {len(warned)} thing(s) worth a look.")


@app.command(name="record-demo")
def record_demo(
    out_file: Annotated[
        Path, typer.Option("--out", help="Where to save (.js for the demo page, or .json).")
    ] = Path("docs/demo/recording.js"),
    question: Annotated[
        list[str] | None, typer.Option("--question", "-q", help="Question to record (repeat).")
    ] = None,
) -> None:
    """Record questions for the static demo page (docs/demo/index.html replays them)."""
    from tempo import demo

    engine = _engine()
    recording = asyncio.run(demo.record(engine, question or demo.QUESTIONS))
    demo.write(recording, out_file)
    kind = "demo models" if recording["demo_mode"] else "your configured models"
    err.print(f"Recorded {len(recording['sessions'])} questions with {kind} to {out_file}.")


@app.command()
def quota(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
) -> None:
    """Show how many free requests each provider has left today."""
    from tempo import budget

    engine = _engine()
    asyncio.run(engine.startup(oneshot=True))
    view = budget.quota_view(engine)
    if as_json:
        out.print_json(json.dumps([q.as_dict() for q in view]))
        return
    table = Table(title="Free requests left today", header_style="bold")
    for column in ("provider", "left today", "per day", "per minute", "resets in", "note"):
        table.add_column(column)
    for q in view:
        left = "-" if q.left_today is None else f"{q.left_today:,}"
        per_day = "-" if q.per_day is None else f"{q.per_day:,}"
        per_minute = "-" if q.per_minute is None else str(q.per_minute)
        style = "red" if q.used_up else None
        table.add_row(
            q.label,
            left,
            per_day,
            per_minute,
            budget.resets_text(q.resets_in_s),
            q.note,
            style=style,
        )
    out.print(table)
    if not view:
        err.print(NO_MODEL_LINE, style="dim", markup=False)
    elif budget.all_used_up(view):
        err.print(
            "Every free quota is used up: Tempo answers with local models (Ollama) and the cache"
            " until the limits reset.",
            style="yellow",
        )


@app.command()
def bench(
    modes: Annotated[
        str, typer.Option("--modes", help="Comma-separated modes to time.")
    ] = "auto,fast,best",
    questions: Annotated[
        int, typer.Option("--questions", "-n", min=1, max=3, help="Standard questions per mode.")
    ] = 3,
    repeat: Annotated[int, typer.Option("--repeat", min=1, max=10, help="Rounds.")] = 1,
    time_budget: TimeOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
) -> None:
    """Time answers per mode with your keys: first token, answer ready, and all stages done.

    Uses your free quota (about 2-6 requests per question). Not logged as questions and no
    answer cache; the timings do update the speed record that routing uses."""
    from tempo import bench as bench_mod

    chosen = [m.strip() for m in modes.split(",") if m.strip()]
    for mode in chosen:
        _check_mode(mode)
    engine = _engine()
    asked = bench_mod.QUESTIONS[:questions]
    err.print(
        f"Timing {len(asked)} question(s) × {repeat} in {', '.join(chosen)} mode "
        f"(about {len(asked) * repeat * len(chosen) * 3} free requests)…",
        style="dim",
    )

    def show(run: Any) -> None:
        if as_json:
            return
        if run.error:
            err.print(f"  {run.mode}: ✗ {run.error}", style="red", markup=False)
        else:
            err.print(
                f"  {run.mode}: first token {run.first_token_s}s · ready {run.ready_s}s · "
                f"done {run.total_s}s · {run.model}",
                style="dim",
                markup=False,
            )

    extra = {"time_budget_s": time_budget} if time_budget else {}
    results = asyncio.run(bench_mod.run_bench(engine, chosen, asked, repeat, on_run=show, **extra))
    summaries = [r.summary() for r in results]
    if as_json:
        out.print_json(json.dumps({"modes": summaries, "speed": engine.speed.table()}))
        return
    table = Table(title="Answer speed (median seconds)", header_style="bold")
    for column in ("mode", "first token", "ready", "done", "slowest", "errors", "models"):
        table.add_column(column)
    for row in summaries:
        cell = lambda v: "-" if v is None else f"{v:.1f}"  # noqa: E731
        table.add_row(
            row["mode"],
            cell(row["first_token_s"]),
            cell(row["ready_s"]),
            cell(row["total_s"]),
            cell(row["slowest_total_s"]),
            str(row["errors"]),
            ", ".join(row["models"]),
        )
    out.print(table)
    err.print(
        "first token: the answer starts · ready: it can be read and copied (checks may go on) "
        "· done: every stage finished",
        style="dim",
    )


@app.command(name="collect")
def collect_data(
    dataset: Annotated[
        list[str] | None,
        typer.Option("--dataset", "-d", help="Only these datasets (repeatable; see --list)."),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", "-n", help="Stop after this many questions.")
    ] = None,
    per_minute: Annotated[
        float, typer.Option("--per-minute", help="Most questions started per minute.")
    ] = 2.0,
    reserve: Annotated[
        float,
        typer.Option(
            "--reserve", help="Share of each model's daily free quota to leave for users (0-0.9)."
        ),
    ] = 0.5,
    provider: Annotated[
        list[str] | None,
        typer.Option(
            "--provider", "-p", help="Only these providers (default: configured, terms not no)."
        ),
    ] = None,
    wait: Annotated[
        bool,
        typer.Option("--wait/--no-wait", help="When free quota runs out, wait (or stop)."),
    ] = True,
    minutes: Annotated[
        float | None,
        typer.Option("--minutes", help="Stop starting new questions after this many minutes."),
    ] = None,
    yes_only: Annotated[
        bool,
        typer.Option(
            "--yes-only",
            help="Only models whose outputs may be training data (tempo-server terms: yes), for "
            "every stage including the judge. Local Apache-2.0 or MIT models through Ollama "
            "qualify.",
        ),
    ] = False,
    show_estimate: Annotated[
        bool, typer.Option("--estimate", help="Only estimate how long collecting will take.")
    ] = False,
    show_status: Annotated[bool, typer.Option("--status", help="Only show progress.")] = False,
    show_list: Annotated[bool, typer.Option("--list", help="Only list the datasets.")] = False,
) -> None:
    """Make Laya training data from openly licensed public questions, slowly and within every
    free limit. Stop any time (Ctrl-C); running it again resumes."""
    from tempo import collect as col
    from tempo.datasets import DATASETS

    if show_list:
        for d in DATASETS.values():
            out.print(f"[bold]{d.name}[/bold]: {d.title}")
            out.print(f"  licence {d.license}: {d.license_url}", markup=False)
            out.print(f"  personal data: {d.personal_data}", markup=False)
        return
    names = dataset or list(DATASETS)
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        raise typer.BadParameter(f"unknown dataset(s): {', '.join(unknown)}")
    if not 0 <= reserve <= 0.9:
        raise typer.BadParameter("--reserve must be between 0 and 0.9")
    engine = _engine()
    if show_estimate:
        est = col.estimate(
            engine.registry,
            engine.store,
            reserve=reserve,
            per_minute=per_minute,
            providers=provider,
            yes_only=yes_only,
        )
        for line in col.describe(est):
            out.print(line, markup=False)
        return
    if show_status:
        counts = engine.store.collect_counts()
        if not counts:
            out.print("Nothing collected yet.")
        for name, by_status in sorted(counts.items()):
            parts = ", ".join(f"{n} {status}" for status, n in sorted(by_status.items()))
            out.print(f"{name}: {parts}", markup=False)
        return
    data_dir = engine.settings.data_dir
    if data_dir is None:
        raise typer.BadParameter(
            "tempo-server collect needs a data directory to resume (TEMPO_DATA_DIR)."
        )
    providers = provider or col.default_providers(engine.registry)
    blocked = [p for p in providers if engine.registry.blocked_for(p, "collect")]
    if blocked:
        raise typer.BadParameter(f"not allowed for tempo-server collect: {', '.join(blocked)}")
    if yes_only:
        providers = [p for p in providers if col.has_yes_models(engine.registry, p)]
    if not providers:
        err.print(
            "No provider to collect with: add a key (see `tempo-server models`). Providers whose "
            "terms say no (see `tempo-server terms`) are left out.",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    items = col.load_items(names, data_dir / "collect")

    async def main() -> col.CollectStats:
        await engine.startup()
        if engine._laya_loading is not None:
            await engine._laya_loading
        err.print(
            f"Collecting with {', '.join(providers)} · {per_minute:g} questions/min · keeping "
            f"{reserve:.0%} of each daily free limit for users · Laya: {engine.laya.status}",
            markup=False,
        )
        return await col.run(
            engine,
            items,
            limit=limit,
            per_minute=per_minute,
            reserve=reserve,
            yes_only=yes_only,
            max_minutes=minutes,
            providers=providers,
            wait=wait,
            say=lambda line: err.print(line, markup=False),
        )

    try:
        stats = asyncio.run(main())
    except KeyboardInterrupt:
        err.print("Stopped. Progress is saved; run `tempo-server collect` again to resume.")
        return
    err.print(
        f"{stats.done} questions answered, {stats.failed} failed, {stats.skipped} skipped "
        f"({stats.already} done earlier) · {stats.stopped}",
        markup=False,
    )


@app.command()
def terms(
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Fetch each provider's terms page and check the quoted sentences are still there.",
        ),
    ] = False,
) -> None:
    """May each provider's outputs be used to train models? Verdict, link and exact sentences,
    and what each free tier may do with prompts (data policy)."""
    from tempo.registry import Registry

    registry = Registry.load()
    if check:
        from tempo.terms import check as check_terms

        colours = {"ok": "green", "changed": "red", "unreachable": "yellow", "unchecked": "dim"}
        for r in asyncio.run(check_terms(registry)):
            c = colours[r.status]
            out.print(f"[bold]{r.provider}[/bold]: [{c}]{r.status}[/{c}] ({r.found} quotes found)")
            out.print(f"  {r.url}", markup=False)
            for quote in r.missing:
                out.print(f"  missing: {quote[:140]}", markup=False)
            if r.note:
                out.print(f"  {r.note}", markup=False)
        return
    colour = {"yes": "green", "no": "red", "unclear": "yellow"}
    for provider in registry.providers.values():
        verdict = provider.training_on_outputs
        checked = provider.training_terms_checked
        checked = f" (checked {checked})" if checked else ""
        state = "" if provider.enabled else " [dim](off by default)[/dim]"
        out.print(
            f"[bold]{provider.label}[/bold]{state}: "
            f"[{colour[verdict]}]{verdict}[/{colour[verdict]}]" + checked
        )
        if provider.training_terms_url:
            out.print(f"  {provider.training_terms_url}", markup=False)
        quote = provider.training_terms_quote or "No terms recorded yet."
        for line in quote.strip().splitlines():
            out.print(f"  {line}", markup=False)
        if provider.data_policy != "unknown" or provider.data_policy_note:
            out.print(f"  Data policy: {provider.data_policy}", markup=False)
            if provider.data_policy_note:
                out.print(f"    {provider.data_policy_note}", markup=False)
        if provider.blocked_for:
            out.print(f"  Never used for: {', '.join(provider.blocked_for)}", markup=False)
        out.print()


def _terms_gate(engine: Engine) -> set[str]:
    """Before every training export: re-read the terms of hosted providers whose outputs count
    as yes. Providers whose quotes changed, or whose pages can't be read, are left out."""
    from tempo.terms import check, export_gate_providers

    logged = {
        row["provider"]
        for row in engine.store.query("SELECT DISTINCT provider FROM calls WHERE status = 'ok'")
    }
    wanted = export_gate_providers(engine.registry) & logged  # only providers in the log
    if not wanted:
        return set()
    err.print(f"Checking terms before export: {', '.join(sorted(wanted))}", markup=False)
    failed = set()
    for result in asyncio.run(check(engine.registry, only=wanted)):
        if result.status != "ok":
            failed.add(result.provider)
            err.print(
                f"  {result.provider}: {result.status}; its rows are left out of this export "
                "(run `tempo-server terms --check`, re-read the terms, update models.yaml).",
                style="yellow",
                markup=False,
            )
        else:
            err.print(f"  {result.provider}: ok ({result.found} quotes found)", markup=False)
    return failed


def _training_users(engine: Engine) -> set[str]:
    """The owner, `tempo-server collect`, and users who opted in to training
    (`tempo-server users consent`)."""
    from tempo.sft import DEFAULT_USERS

    return set(DEFAULT_USERS) | engine.accounts.consented_users()


def _export_writing(kind: str, out_dir: Path, test_percent: int) -> None:
    from tempo import sft

    engine = _engine()
    settings = engine.settings
    stats = sft.export(
        engine.store,
        engine.registry,
        out_dir,
        kind,
        test_percent=test_percent,
        users=_training_users(engine),
        unverified=_terms_gate(engine),
        min_public=settings.min_public_share,
        max_self=settings.max_self_share,
        include_mcp=settings.train_on_mcp,
    )
    count = stats.rows if kind == "sft" else stats.pairs
    what = "checked answers" if kind == "sft" else "preference pairs"
    err.print(f"Wrote {count} {what} to {out_dir}/ ({dict(stats.splits)})", markup=False)
    notes = {
        "final answer did not pass its check": stats.skipped_not_passed,
        "👎 from the user": stats.skipped_feedback,
        "text from a model or provider that is not 'yes' (tempo-server terms)": stats.skipped_terms,
        "asked by a user who has not opted in (tempo-server users consent)": stats.skipped_user,
        "asked by an AI assistant over MCP (TEMPO_TRAIN_ON_MCP=1 to include)": stats.skipped_mcp,
    }
    if kind == "pairs":
        notes["no failed draft to pair with"] = stats.skipped_no_rejected
    for reason, n in notes.items():
        if n:
            err.print(f"  left out {n}: {reason}", markup=False)
    rep = stats.repetition
    err.print(
        f"  repetition: distinct word pairs {rep.get('distinct_2')}, "
        f"repeated 4-word sequences {rep.get('repeated_4')}",
        markup=False,
    )
    for note in sft.mix_warnings(stats, settings.min_public_share, settings.max_self_share):
        err.print(f"  warning: {note}", style="yellow", markup=False)


@app.command(name="export-sft")
def export_sft(
    out_dir: Annotated[Path, typer.Option("--out", "-o", help="Folder to write.")],
    test_percent: Annotated[int, typer.Option("--test-percent", help="Held-out share.")] = 10,
) -> None:
    """Export question -> checked final answer, for fine-tuning Tempo-Core (only "yes" rows)."""
    _export_writing("sft", out_dir, test_percent)


@app.command(name="export-pairs")
def export_pairs(
    out_dir: Annotated[Path, typer.Option("--out", "-o", help="Folder to write.")],
    test_percent: Annotated[int, typer.Option("--test-percent", help="Held-out share.")] = 10,
) -> None:
    """Export (question, chosen = answer that passed, rejected = draft that failed) for DPO."""
    _export_writing("pairs", out_dir, test_percent)


@app.command(name="export-laya")
def export_laya(
    out_dir: Annotated[
        Path, typer.Option("--out", "-o", help="Folder to write train.jsonl / test.jsonl to.")
    ] = Path("laya-dataset"),
    test_percent: Annotated[
        int, typer.Option("--test-percent", help="Share of questions held out for testing.")
    ] = 10,
    include_unclear: Annotated[
        bool,
        typer.Option(
            "--include-unclear",
            help="Also use outputs from providers whose terms are unclear (read them first: "
            "tempo-server terms).",
        ),
    ] = False,
) -> None:
    """Export logged decisions as a dataset for Laya's official fine-tuning notebook."""
    from tempo.tuning import export

    engine = _engine()
    stats = export(
        engine.store,
        engine.registry,
        out_dir,
        test_percent=test_percent,
        include_unclear=include_unclear,
        unverified=_terms_gate(engine),
        users=_training_users(engine),
        include_mcp=engine.settings.train_on_mcp,
    )
    err.print(
        f"Wrote {stats.rows} rows ({stats.decisions} labelled decisions from "
        f"{stats.questions} questions) to {out_dir}/",
        markup=False,
    )
    for workflow, count in sorted(stats.by_workflow.items()):
        err.print(f"  {workflow}: {count}", markup=False)
    if stats.skipped_terms:
        err.print(
            f"Left out {stats.skipped_terms} rows with text from providers whose terms say no"
            + ("" if include_unclear else " or are unclear")
            + ".",
            markup=False,
        )
    if stats.unclear_terms_providers:
        providers = ", ".join(sorted(stats.unclear_terms_providers))
        err.print(
            f"Terms are unclear for: {providers}. Read them with `tempo-server terms`"
            + ("." if include_unclear else "; --include-unclear then keeps their rows."),
            style="yellow",
            markup=False,
        )
    err.print(f"See {out_dir}/README.md for how to load it in the notebook.", markup=False)


sandbox_app = typer.Typer(
    help="The WebAssembly sandbox that runs code and maths answers (docs/SANDBOX.md)."
)
app.add_typer(sandbox_app, name="sandbox")
LanguageOption = Annotated[
    str | None,
    typer.Option("--language", "-l", help="python or javascript (default: both)."),
]


def _sandbox() -> Any:
    from tempo.sandbox import Sandbox, sandbox_home

    settings = Settings.from_env()
    return Sandbox(sandbox_home(settings.data_dir)), settings


@sandbox_app.command("install")
def sandbox_install(language: LanguageOption = None) -> None:
    """Download (checked by SHA-256), unpack and compile the Python and JavaScript runtimes."""
    from tempo.sandbox import LANGUAGES, RUNTIMES, SandboxUnavailable

    box, _ = _sandbox()
    chosen = [language] if language else list(LANGUAGES)
    for lang in chosen:
        if lang not in RUNTIMES:
            raise typer.BadParameter("language must be python or javascript")
        try:
            box.install(lang, say=lambda text: err.print(text, markup=False))
        except (SandboxUnavailable, OSError) as exc:
            err.print(f"Could not install the {lang} sandbox: {exc}", style="red", markup=False)
            raise typer.Exit(1) from exc
    err.print(f"Sandbox ready in {box.home}", markup=False)


@sandbox_app.command("status")
def sandbox_status() -> None:
    """Whether the sandbox is on, which runtimes are installed, and the limits."""
    from tempo.sandbox import RUNTIMES, wasmtime_version

    box, settings = _sandbox()
    state = "off (TEMPO_SANDBOX=off)" if settings.sandbox == "off" else "on when installed"
    out.print(f"Sandbox: {state}; maths: {settings.sandbox_math}", markup=False)
    out.print(
        f"Wasmtime: {wasmtime_version() or 'not installed'}; folder: {box.home}", markup=False
    )
    for lang, rt in RUNTIMES.items():
        mark = "installed" if box.installed(lang) else "not installed"
        out.print(f"  {lang}: {rt.version} ({rt.licence}) - {mark}", markup=False)
    out.print(
        f"Limits: {settings.sandbox_timeout_s:g}s, {settings.sandbox_memory_mb} MB memory, "
        f"{settings.sandbox_output_kb} KB output",
        markup=False,
    )


@sandbox_app.command("run")
def sandbox_run(
    file: Annotated[Path, typer.Argument(help="A .py or .js file to run.", exists=True)],
    timeout: Annotated[float, typer.Option(help="Seconds.")] = 10.0,
) -> None:
    """Run a file in the sandbox and show what happened (for trying it out)."""
    from tempo.sandbox import Limits, SandboxUnavailable

    box, settings = _sandbox()
    lang = "javascript" if file.suffix in (".js", ".mjs") else "python"
    limits = Limits(
        timeout_s=timeout,
        memory_mb=settings.sandbox_memory_mb,
        output_kb=settings.sandbox_output_kb,
    )
    try:
        result = box.run(lang, file.read_text(encoding="utf-8"), limits=limits)
    except SandboxUnavailable as exc:
        err.print(str(exc), style="red", markup=False)
        raise typer.Exit(1) from exc
    if result.stdout:
        out.print(result.stdout, end="", markup=False, highlight=False)
    if result.stderr:
        err.print(result.stderr, end="", markup=False, highlight=False)
    err.print(f"[{lang}: {result.summary}, {result.duration_ms} ms]", style="dim", markup=False)
    raise typer.Exit(0 if result.ok else 1)


laya_app = typer.Typer(help="Laya, the fast decision-maker: status and comparison with rules.")
app.add_typer(laya_app, name="laya")


@laya_app.command("status")
def laya_status() -> None:
    """Show whether Laya is installed, which decisions it controls, and logged predictions."""
    import importlib.util

    from tempo.config import LAYA_DECISIONS
    from tempo.laya_decider import checkpoint_name
    from tempo.laya_runtime import default_threads

    engine = _engine()
    s = engine.settings
    installed = importlib.util.find_spec("laya") is not None
    onnx = importlib.util.find_spec("onnxruntime") is not None
    err.print(
        f"Laya package installed: {'yes' if installed else 'no'} · ONNX Runtime: "
        f"{'yes' if onnx else 'no'}",
        markup=False,
    )
    err.print(
        f"TEMPO_LAYA: {s.laya} · runs {checkpoint_name(s)} on CPU with "
        f"{s.laya_threads or default_threads()} threads · time limit "
        + (f"{s.laya_timeout_ms:.0f} ms" if s.laya_timeout_ms else "measured when it loads"),
        markup=False,
    )
    counts = {
        r["name"]: r
        for r in engine.store.query(
            "SELECT name, COUNT(*) AS n, SUM(laya_status IN ('ok','late')) AS predicted, "
            "SUM(used = 'laya') AS decided FROM decisions GROUP BY name"
        )
    }
    table = Table(header_style="bold")
    for column in ("decision", "mode", "logged", "Laya predicted", "Laya decided"):
        table.add_column(column)
    for name in LAYA_DECISIONS:
        row = counts.get(name, {})
        table.add_row(
            name,
            engine.settings.laya_mode(name),
            str(row.get("n", 0)),
            str(row.get("predicted") or 0),
            str(row.get("decided") or 0),
        )
    out.print(table)


@laya_app.command("compare")
def laya_compare(
    test_percent: Annotated[
        int, typer.Option("--test-percent", help="Must match the export split.")
    ] = 10,
) -> None:
    """Score Laya and the rules against outcome labels on held-out questions. Decisions set
    to "auto" in TEMPO_LAYA_TAKEOVER switch to Laya once it wins here on 50+ rows."""
    from tempo.laya_decider import MIN_COMPARE_ROWS, checkpoint_name
    from tempo.tuning import compare

    engine = _engine()
    checkpoint = checkpoint_name(engine.settings)
    results = compare(
        engine.store, engine.registry, test_percent=test_percent, laya_model=checkpoint
    )
    table = Table(
        title=f"Laya ({checkpoint}) vs rules on held-out questions",
        header_style="bold",
    )
    for column in ("decision", "rows", "Laya", "rules", "verdict"):
        table.add_column(column)
    winners = []
    for r in results:
        if r.n == 0:
            verdict = "no data yet"
        elif r.n < MIN_COMPARE_ROWS:
            verdict = f"need {MIN_COMPARE_ROWS}+ rows"
        elif r.laya_wins:
            verdict = "[green]Laya better[/green]"
            winners.append(r.decision)
        else:
            verdict = "rules better"
        table.add_row(
            r.decision, str(r.n), f"{r.laya_accuracy:.2f}", f"{r.rules_accuracy:.2f}", verdict
        )
    out.print(table)
    if winners:
        err.print(
            "Hand these to Laya with TEMPO_LAYA_TAKEOVER=" + ",".join(f"{w}=auto" for w in winners),
            markup=False,
        )


users_app = typer.Typer(help="Tempo users: each gets an API key for the web app and API.")
keys_app = typer.Typer(help="Your own provider keys, stored encrypted (bring your own key).")
app.add_typer(users_app, name="users")
app.add_typer(keys_app, name="keys")


@users_app.command("add")
def users_add(name: Annotated[str, typer.Argument(help="User name.")]) -> None:
    """Create a user and print their Tempo API key (shown once)."""
    engine = _engine()
    try:
        user, api_key = engine.accounts.create_user(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    err.print(f"Created user {user.name}. Their API key (shown only now):", markup=False)
    out.print(api_key, markup=False)
    err.print(
        "Once any user exists, the API and web app require a key (Authorization: Bearer <key>).",
        style="dim",
        markup=False,
    )


@users_app.command("list")
def users_list() -> None:
    """List users and how many provider keys each has stored."""
    rows = _engine().accounts.list_users()
    if not rows:
        err.print("No users: the server runs in single-user local mode.", style="dim")
        return
    table = Table(header_style="bold")
    table.add_column("user")
    table.add_column("provider keys", justify="right")
    for row in rows:
        table.add_row(row["name"], str(row["keys"]))
    out.print(table)


@users_app.command("remove")
def users_remove(name: Annotated[str, typer.Argument(help="User name.")]) -> None:
    """Delete a user and their stored provider keys."""
    if not _engine().accounts.delete_user(name):
        raise typer.BadParameter(f"no user named {name!r}")
    err.print(f"Removed {name}.", markup=False)


@users_app.command("consent")
def users_consent(
    name: Annotated[str, typer.Argument(help="User name.")],
    on: Annotated[
        bool, typer.Option("--on/--off", help="Opt in to training use, or withdraw.")
    ] = True,
) -> None:
    """Record a user's choice: may their questions be used as training data? Off by default;
    withdrawing keeps them out of every later export."""
    engine = _engine()
    user = engine.accounts.find(name)
    if user is None:
        raise typer.BadParameter(f"no user named {name!r}")
    engine.accounts.set_consent(user.id, on)
    state = "opted in to" if on else "withdrawn from"
    err.print(f"{name} has {state} training use of their questions.", markup=False)


@users_app.command("forget")
def users_forget(
    name: Annotated[str, typer.Argument(help="User name.")],
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask to confirm.")] = False,
) -> None:
    """Delete everything logged for a user's questions (answers, decisions, feedback)."""
    engine = _engine()
    user = engine.accounts.find(name)
    if user is None:
        raise typer.BadParameter(f"no user named {name!r}")
    if not yes and not typer.confirm(f"Delete every logged question of {name}?"):
        raise typer.Exit(1)
    count = engine.accounts.delete_data(user.id)
    err.print(f"Deleted {count} logged questions of {name}.", markup=False)


UserOption = Annotated[
    str | None, typer.Option("--user", help="Whose keys (default: the local user).")
]


def _user_id(engine: Engine, name: str | None) -> str:
    from tempo.accounts import LOCAL_USER

    if not name or name == LOCAL_USER:
        return LOCAL_USER
    user = engine.accounts.find(name)
    if user is None:
        raise typer.BadParameter(f"no user named {name!r}")
    return user.id


@keys_app.command("add")
def keys_add(
    provider: Annotated[str, typer.Argument(help="Provider id, e.g. groq.")],
    user: UserOption = None,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Store without checking it with the provider.")
    ] = False,
) -> None:
    """Store your own key for a provider (entered at a hidden prompt, or piped on stdin)."""
    from tempo.accounts import fingerprint
    from tempo.sync import verify_key

    engine = _engine()
    info = engine.registry.providers.get(provider)
    if info is None or not info.key_env:
        keyed = ", ".join(p.id for p in engine.registry.providers.values() if p.key_env)
        raise typer.BadParameter(f"unknown provider; use one of: {keyed}")
    if sys.stdin.isatty():
        import getpass

        api_key = getpass.getpass(f"{info.label} API key (hidden): ").strip()
    else:
        api_key = sys.stdin.readline().strip()
    if not api_key:
        raise typer.BadParameter("no key given")
    verified = None
    if not no_verify:
        verified = asyncio.run(verify_key(engine.registry, provider, api_key))
        if verified is False:
            err.print(f"{info.label} rejected this key; nothing stored.", style="red")
            raise typer.Exit(1)
    engine.accounts.set_key(_user_id(engine, user), provider, api_key, verified)
    if info.byok_only:  # the server has no key of its own: read the model list with this one
        asyncio.run(engine.refresh_provider(provider, api_key))
    note = (
        "verified" if verified else "stored (could not verify now)" if not no_verify else "stored"
    )
    err.print(f"{info.label} key {fingerprint(api_key)} {note}.", markup=False)


@keys_app.command("list")
def keys_list(user: UserOption = None) -> None:
    """Show which providers have a stored key (last four characters only)."""
    engine = _engine()
    rows = engine.accounts.key_info(_user_id(engine, user))
    if not rows:
        err.print("No keys stored. Add one with: tempo-server keys add groq", style="dim")
        return
    table = Table(header_style="bold")
    for column in ("provider", "key", "verified"):
        table.add_column(column)
    for row in rows:
        verified = {1: "yes", 0: "no"}.get(row["verified"], "unknown")
        table.add_row(row["provider"], row["fingerprint"], verified)
    out.print(table)


@keys_app.command("remove")
def keys_remove(
    provider: Annotated[str, typer.Argument(help="Provider id.")], user: UserOption = None
) -> None:
    """Delete a stored provider key."""
    engine = _engine()
    if not engine.accounts.delete_key(_user_id(engine, user), provider):
        raise typer.BadParameter(f"no stored key for {provider!r}")
    err.print(f"Removed the {provider} key.", markup=False)


train_app = typer.Typer(
    help="Train Tempo's own models on Kaggle's free GPUs: prepare the upload, dry-run the loop "
    "(docs/TRAINING.md)."
)
app.add_typer(train_app, name="train")


def _print_findings(findings: list[Any]) -> None:
    styles = {"error": "red", "mix": "yellow", "warning": "yellow"}
    for finding in findings:
        label = {"error": "error", "mix": "data mix", "warning": "note"}[finding.level]
        err.print(
            f"  {label}: {finding.text}", style=styles[finding.level], markup=False, soft_wrap=True
        )


@train_app.command("prepare")
def train_prepare(
    out_dir: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Pack folder (default: <data dir>/training/<date>)."),
    ] = None,
    test_percent: Annotated[
        int, typer.Option("--test-percent", help="Held-out share, the same for every export.")
    ] = 10,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Pack even when the data mix is off (recorded in the pack). Licence problems "
            "always stop it.",
        ),
    ] = False,
) -> None:
    """Export, check data mix and licences, pack one zip for Kaggle, print the upload steps."""
    from tempo import training

    engine = _engine()
    out_dir = out_dir or training.default_out(engine)
    report = training.prepare(
        engine,
        out_dir,
        test_percent=test_percent,
        users=_training_users(engine),
        unverified=_terms_gate(engine),
        force=force,
        include_mcp=engine.settings.train_on_mcp,
    )
    counts = report.manifest["counts"]
    err.print(f"Exported to {report.folder}/", markup=False, soft_wrap=True)
    for kind, split in counts.items():
        err.print(
            f"  {kind}: {split['train']} train, {split['test']} held out",
            markup=False,
            soft_wrap=True,
        )
    mix = report.manifest["mix"]
    err.print(
        f"  data mix: {mix['public_share']:.0%} public or human (min "
        f"{mix['min_public_share']:.0%}), {mix['self_share']:.0%} from an earlier Tempo-Core "
        f"(max {mix['max_self_share']:.0%})",
        markup=False,
        soft_wrap=True,
    )
    for kind, info in report.manifest["licences"].items():
        sources = ", ".join(f"{n} {s['license']}" for n, s in info["sources"].items())
        if sources:
            err.print(f"  {kind} licences: {sources}", markup=False, soft_wrap=True)
    _print_findings(report.findings)
    if report.zip_path is None:
        why = "licence or data problems" if report.errors else "the data mix (--force to pack)"
        err.print(f"Not packed because of {why}.", style="red", markup=False, soft_wrap=True)
        raise typer.Exit(1)
    size = report.zip_path.stat().st_size / 1e6
    err.print(f"Packed {report.zip_path} ({size:.1f} MB)\n", markup=False, soft_wrap=True)
    print(training.upload_steps(report))


@train_app.command("dry-run")
def train_dry_run(
    work: Annotated[
        Path | None,
        typer.Option("--work", help="Folder for everything it makes (default: a temporary one)."),
    ] = None,
    questions: Annotated[int, typer.Option("--questions", help="Demo questions.")] = 40,
) -> None:
    """Every step of the loop on this CPU with tiny models, no keys: demo data, prepare, both
    notebooks, import, compare, promote."""
    import tempfile

    from tempo import dry_run

    def say(text: str) -> None:
        err.print(text, markup=False, highlight=False, soft_wrap=True)

    try:
        if work is not None:
            outcome = dry_run.run(work, questions, say)
        else:
            with tempfile.TemporaryDirectory(prefix="tempo-dry-run-") as folder:
                outcome = dry_run.run(Path(folder), questions, say)
    except dry_run.DryRunError as exc:
        err.print(str(exc), style="red", markup=False, soft_wrap=True)
        raise typer.Exit(1) from exc
    out.print_json(data=outcome)


@train_app.command("notebooks")
def train_notebooks(
    out_dir: Annotated[Path, typer.Option("--out", "-o", help="Folder to write.")] = Path(
        "training"
    ),
) -> None:
    """Write the two Kaggle notebooks (tempo_core.ipynb, tempo_router_judge.ipynb)."""
    from tempo.notebooks import write_all

    for path in write_all(out_dir):
        err.print(f"Wrote {path}", markup=False, soft_wrap=True)


@app.command(name="mcp")
def mcp_command(
    http: Annotated[
        bool,
        typer.Option(
            "--http", help="Serve MCP over HTTP at /mcp (needs a Tempo key) instead of stdio."
        ),
    ] = False,
    host: Annotated[str, typer.Option(help="Interface to bind (with --http).")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port (with --http).")] = 8001,
) -> None:
    """Run Tempo-server as an MCP server for AI assistants (docs/MCP.md): stdio by default."""
    from tempo.mcp_server import build_server, http_app

    settings = Settings.from_env()
    engine = Engine.from_settings(settings)
    if not http:  # stdout carries the protocol: everything else goes to stderr

        async def run_stdio() -> None:
            await engine.startup()
            await build_server(engine, settings.api_key).run_stdio_async()

        asyncio.run(run_stdio())
        return
    if not settings.api_key and not engine.accounts.has_users():
        err.print(
            "MCP over HTTP needs a Tempo key: create one with `tempo-server users add <name>` "
            "(or set TEMPO_API_KEY), then send it as Authorization: Bearer <key>.",
            style="red",
            markup=False,
        )
        raise typer.Exit(1)
    import uvicorn

    async def run_http() -> None:
        await engine.startup()
        app = http_app(engine, settings.api_key, host=host)
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()

    err.print(f"Tempo-server MCP on http://{host}:{port}/mcp (needs a Tempo key)", markup=False)
    asyncio.run(run_http())


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


def _utf8_console() -> None:
    """Windows consoles and pipes may use a legacy code page that can't print ▸ or ✗."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _utf8_console()
    for note in paths.notices:
        err.print(note, style="yellow", markup=False)
    app()


ALIAS_NOTICE = (
    "Note: `tempo` is a short alias that will be removed before Tempo-server 1.0 (Grafana Tempo "
    "also has a `tempo` program). Use `tempo-server` instead."
)


def main_alias() -> None:
    """The `tempo` command: the same as `tempo-server`, with a notice that it will go away."""
    _utf8_console()
    if not os.environ.get("TEMPO_NO_ALIAS_NOTICE"):
        err.print(ALIAS_NOTICE, style="dim", markup=False)
    main()


if __name__ == "__main__":
    main()
