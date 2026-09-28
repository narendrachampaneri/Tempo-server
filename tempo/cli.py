"""The ``tempo`` command: ask questions, chat, list models, run the server.

Trace lines go to stderr and the answer to stdout, so ``tempo ask "..." > answer.md`` works.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
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
    """Stream the answer to stdout and the thinking window to stderr.

    A later stage that replaces the draft is printed again below it; if the final answer
    differs from what was streamed last, it is printed once more at the end.
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
        if event.type == "answer_final" and data["answer"].strip() != shown.strip():
            close_answer()
            if show_trace:
                err.rule(style="dim")
            sys.stdout.write(data["answer"].rstrip() + "\n")
            sys.stdout.flush()
            shown = data["answer"]

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


@app.command()
def models() -> None:
    """List models and whether each one is ready to use."""
    engine = _engine()
    asyncio.run(engine.startup(oneshot=True))
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

    missing = [p for p in registry.providers.values() if not registry.is_configured(p.id)]
    if missing:
        out.print("\nTo enable more providers, set these (free) in your environment or .env:")
        for p in missing:
            env = p.key_env or p.base_env
            out.print(f"  {env:<20} {p.label:<26} {p.signup_url or ''}", markup=False)


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
    status = asyncio.run(RegistrySync(engine.registry, engine.health).run())
    table = Table(title="Provider sync", header_style="bold")
    for column in ("provider", "status", "models listed", "new", "no longer listed"):
        table.add_column(column)
    for s in status.values():
        state = "[green]ok[/green]" if s.ok else f"[red]{s.error}[/red]"
        table.add_row(s.provider, state, str(s.listed), str(len(s.added)), str(len(s.removed)))
    out.print(table)
    if not status:
        err.print("No providers configured.", style="dim")


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
        raise typer.BadParameter("tempo collect needs a data directory to resume (TEMPO_DATA_DIR).")
    providers = provider or col.default_providers(engine.registry)
    if not providers:
        err.print(
            "No provider to collect with: add a key (see `tempo models`). Providers whose terms "
            "say no (see `tempo terms`) are left out.",
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
            providers=providers,
            wait=wait,
            say=lambda line: err.print(line, markup=False),
        )

    try:
        stats = asyncio.run(main())
    except KeyboardInterrupt:
        err.print("Stopped. Progress is saved; run `tempo collect` again to resume.")
        return
    err.print(
        f"{stats.done} questions answered, {stats.failed} failed, {stats.skipped} skipped "
        f"({stats.already} done earlier) · {stats.stopped}",
        markup=False,
    )


@app.command()
def terms() -> None:
    """May each provider's outputs be used to train models? Verdict, link and exact sentences."""
    from tempo.registry import Registry

    registry = Registry.load()
    colour = {"yes": "green", "no": "red", "unclear": "yellow"}
    for provider in registry.providers.values():
        verdict = provider.training_on_outputs
        checked = provider.training_terms_checked
        checked = f" (checked {checked})" if checked else ""
        out.print(
            f"[bold]{provider.label}[/bold]: [{colour[verdict]}]{verdict}[/{colour[verdict]}]"
            + checked
        )
        if provider.training_terms_url:
            out.print(f"  {provider.training_terms_url}", markup=False)
        quote = provider.training_terms_quote or "No terms recorded yet."
        for line in quote.strip().splitlines():
            out.print(f"  {line}", markup=False)
        out.print()


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
            "tempo terms).",
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
            f"Terms are unclear for: {providers}. Read them with `tempo terms`"
            + ("." if include_unclear else "; --include-unclear then keeps their rows."),
            style="yellow",
            markup=False,
        )
    err.print(f"See {out_dir}/README.md for how to load it in the notebook.", markup=False)


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
    note = (
        "verified" if verified else "stored (could not verify now)" if not no_verify else "stored"
    )
    err.print(f"{info.label} key …{api_key[-4:]} {note}.", markup=False)


@keys_app.command("list")
def keys_list(user: UserOption = None) -> None:
    """Show which providers have a stored key (last four characters only)."""
    engine = _engine()
    rows = engine.accounts.key_info(_user_id(engine, user))
    if not rows:
        err.print("No keys stored. Add one with: tempo keys add groq", style="dim")
        return
    table = Table(header_style="bold")
    for column in ("provider", "key", "verified"):
        table.add_column(column)
    for row in rows:
        verified = {1: "yes", 0: "no"}.get(row["verified"], "unknown")
        table.add_row(row["provider"], f"…{row['last4']}", verified)
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
