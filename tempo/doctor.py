"""`tempo-server doctor`: check the install and print a clear fix for each problem.

Checks the Python version, that the command is on PATH, the data folder, keys, which providers
answer over the network, Ollama, and the server port. Reachability is checked with a plain
request to each provider's model-list address, without a key: no key is sent and no model runs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import stat
import sys
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx

from tempo import __version__, paths
from tempo.accounts import LOCAL_USER, fingerprint
from tempo.registry import OLLAMA_DEFAULT_BASE
from tempo.setup import probe_ollama
from tempo.sync import MODEL_LISTS, RegistrySync

if TYPE_CHECKING:
    from tempo.engine import Engine

Status = Literal["ok", "warn", "fail", "info"]
MIN_PYTHON = (3, 11)
REACH_TIMEOUT_S = 6.0
INTERNET_PROBE = MODEL_LISTS["openrouter"]


@dataclass
class Check:
    area: str
    status: Status
    message: str
    fix: str = ""


def check_python(version: tuple[int, ...] | None = None) -> Check:
    version = version or sys.version_info[:3]
    shown = ".".join(str(v) for v in version[:3])
    if tuple(version[:2]) < MIN_PYTHON:
        return Check(
            "Python",
            "fail",
            f"Python {shown} is too old (3.11 or newer needed).",
            "Install Python 3.11+ (https://www.python.org/downloads/), then reinstall: "
            "uv tool install --python 3.12 tempo-server",
        )
    return Check("Python", "ok", f"Python {shown} on {sys.platform}; Tempo-server {__version__}.")


def check_command() -> Check:
    found = shutil.which("tempo-server")
    if found:
        return Check("Command", "ok", f"tempo-server is on PATH ({found}).")
    return Check(
        "Command",
        "warn",
        "tempo-server is not on PATH (it ran another way, e.g. python -m).",
        "Run `uv tool update-shell` or `pipx ensurepath`, then open a new terminal.",
    )


def check_data_dir(engine: Engine) -> list[Check]:
    data_dir = engine.settings.data_dir
    if data_dir is None:
        return [
            Check(
                "Data folder",
                "warn",
                "TEMPO_DATA_DIR=memory: keys, logs and quota counters are lost on exit.",
                f"Unset TEMPO_DATA_DIR to use the normal folder ({paths.default_data_dir()}).",
            )
        ]
    checks: list[Check] = []
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".doctor-", delete=True):
            pass
        checks.append(Check("Data folder", "ok", f"{data_dir} (writable)."))
    except OSError as exc:
        checks.append(
            Check(
                "Data folder",
                "fail",
                f"{data_dir} is not writable: {exc.strerror or exc}.",
                "Fix the folder's permissions, or set TEMPO_DATA_DIR to a folder you own.",
            )
        )
        return checks
    legacy = paths.legacy_data_dir()
    if legacy.is_dir() and legacy.resolve() != data_dir.resolve():
        checks.append(
            Check(
                "Data folder",
                "warn",
                f"An old data folder is still at {legacy}.",
                f"Nothing in it is used any more: delete {legacy} once you no longer need it.",
            )
        )
    secret = data_dir / "secret.key"
    if os.name == "posix" and secret.exists():
        mode = stat.S_IMODE(secret.stat().st_mode)
        if mode & 0o077:
            checks.append(
                Check(
                    "Data folder",
                    "fail",
                    f"The key-vault secret is readable by other users (mode {mode:o}).",
                    f"chmod 600 '{secret}'",
                )
            )
    return checks


def check_keys(engine: Engine) -> list[Check]:
    registry = engine.registry
    owner = engine.access_for(LOCAL_USER)
    checks: list[Check] = []
    ready = []
    for provider in registry.providers.values():
        if not provider.key_env or provider.id == "mock":
            continue
        vault = owner.user_keys.get(provider.id)
        env_key = registry._env.get(provider.key_env, "").strip()
        source = (
            f"vault {fingerprint(vault)}"
            if vault
            else f"{provider.key_env} {fingerprint(env_key)}"
            if env_key and not provider.byok_only
            else None
        )
        if source is None:
            continue
        if not registry.is_enabled(provider.id):
            checks.append(
                Check(
                    "Keys",
                    "info",
                    f"{provider.label}: a key is there ({source}) but the provider is off.",
                    provider.disabled_note or "",
                )
            )
        elif not registry.is_configured(provider.id, owner):
            checks.append(
                Check(
                    "Keys",
                    "warn",
                    f"{provider.label}: a key is there ({source}) but something else is missing.",
                    "Run `tempo-server setup --only " + provider.id + "` (for example Cloudflare "
                    "needs CLOUDFLARE_ACCOUNT_ID).",
                )
            )
        else:
            ready.append(f"{provider.label} ({source})")
    if ready:
        checks.insert(0, Check("Keys", "ok", "Ready: " + ", ".join(ready) + "."))
    else:
        checks.insert(
            0,
            Check(
                "Keys",
                "warn",
                "No provider key yet: only local models (Ollama) or demo mode can answer.",
                "Run `tempo-server setup` to add a free key (Groq or Google take a minute).",
            ),
        )
    return checks


async def _reach(client: httpx.AsyncClient, url: str) -> str | None:
    """None when the host answered (any HTTP status), else the error."""
    try:
        await client.get(url)
        return None
    except httpx.HTTPError as exc:
        return type(exc).__name__


async def check_reachable(
    engine: Engine, transport: httpx.AsyncBaseTransport | None = None
) -> list[Check]:
    registry = engine.registry
    owner = engine.access_for(LOCAL_USER)
    sync = RegistrySync(registry)
    targets: dict[str, str] = {}
    for provider in registry.providers.values():
        if provider.local or provider.id == "mock":
            continue
        if registry.is_configured(provider.id, owner):
            url = sync.list_url(provider.id)
            if url:
                targets[provider.label] = url
    if not targets:
        targets["the internet (openrouter.ai)"] = INTERNET_PROBE
    async with httpx.AsyncClient(transport=transport, timeout=REACH_TIMEOUT_S) as client:
        errors = await asyncio.gather(*(_reach(client, url) for url in targets.values()))
    checks = []
    for label, error in zip(targets, errors, strict=True):
        if error is None:
            checks.append(Check("Network", "ok", f"{label} is reachable."))
        else:
            checks.append(
                Check(
                    "Network",
                    "fail",
                    f"{label} is not reachable ({error}).",
                    "Check your internet connection, firewall or proxy (HTTPS_PROXY), "
                    "then run `tempo-server doctor` again.",
                )
            )
    return checks


async def check_ollama(engine: Engine, transport: httpx.AsyncBaseTransport | None = None) -> Check:
    provider = engine.registry.providers.get("ollama")
    env_name = (provider.base_env if provider else None) or "OLLAMA_API_BASE"
    configured = os.environ.get(env_name, "").strip()
    base = configured or OLLAMA_DEFAULT_BASE
    count = await probe_ollama(base, transport)
    if count is None:
        return Check(
            "Ollama",
            "info",
            f"Ollama is not running at {base} (optional).",
            "For local answers: install Ollama (https://ollama.com/download), start it, then "
            "`ollama pull qwen3:1.7b` and `tempo-server setup --only ollama`.",
        )
    if not configured:
        return Check(
            "Ollama",
            "warn",
            f"Ollama is running at {base}, but Tempo-server is not set to use it.",
            "Run `tempo-server setup --only ollama`.",
        )
    if count == 0:
        return Check(
            "Ollama",
            "warn",
            f"Ollama is running at {base} with no models.",
            "Pull a small model: ollama pull qwen3:1.7b",
        )
    return Check("Ollama", "ok", f"Ollama is running at {base} with {count} model(s).")


def _tempo_on(port: int, host: str) -> bool:
    try:
        response = httpx.get(f"http://{host}:{port}/health", timeout=2.0, trust_env=False)
        return response.json().get("status") == "ok" and "models_ready" in response.json()
    except (httpx.HTTPError, ValueError):
        return False


def check_port(port: int, host: str = "127.0.0.1") -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":  # on Windows this would let two programs share the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            if _tempo_on(port, host):
                return Check("Port", "ok", f"Tempo-server is already running on port {port}.")
            return Check(
                "Port",
                "fail",
                f"Port {port} is used by another program.",
                f"Start on another port: tempo-server serve --port {port + 1}",
            )
    return Check("Port", "ok", f"Port {port} is free for `tempo-server serve`.")


def check_sandbox(engine: Engine) -> Check:
    from tempo.sandbox import LANGUAGES, wasmtime_version

    box = engine.sandbox
    if box is None:
        return Check(
            "Sandbox", "info", "Off (TEMPO_SANDBOX=off): code and maths answers aren't run."
        )
    if wasmtime_version() is None:
        return Check(
            "Sandbox",
            "warn",
            "The wasmtime package is missing, so code and maths answers aren't run.",
            "Reinstall tempo-server (pipx install --force tempo-server).",
        )
    missing = [lang for lang in LANGUAGES if not box.installed(lang)]
    if missing:
        return Check(
            "Sandbox",
            "info",
            f"Not installed: {', '.join(missing)} (code and maths answers aren't run yet).",
            "tempo-server sandbox install  (about 16 MB, once)",
        )
    return Check("Sandbox", "ok", f"Python and JavaScript ready in {box.home}.")


LAYA_DOWNLOAD = "about 846 MB, once"  # the stock English checkpoint (tempo/laya_runtime.py)


def check_laya(engine: Engine) -> Check:
    """Laya: installed or not (with the exact install command), whether the checkpoint is on
    disk (found without touching the network), and what the server measured when it last
    loaded it: the runner and the time per kind of decision."""
    from tempo import laya_runtime
    from tempo.laya_decider import cache_dir

    s = engine.settings
    if s.laya == "off":
        return Check("Laya", "info", "Off (TEMPO_LAYA=off): the rules make every decision.")
    backend = "torch" if s.laya_backend == "auto" else s.laya_backend
    try:
        laya_runtime.ensure_installed(backend)
    except ImportError:
        gpu = shutil.which("nvidia-smi") is not None
        return Check(
            "Laya",
            "info",
            "Not installed (optional): the rules make every decision. Laya is a learned "
            f"decision-maker; its checkpoint is {LAYA_DOWNLOAD}.",
            f"{laya_runtime.install_command(gpu)}, then restart `tempo-server serve`.",
        )
    status = laya_runtime.read_status(cache_dir(s))
    if not status:
        on_disk = laya_runtime.local_checkpoint(s.laya_checkpoint, s.laya_model) is not None
        where = (
            "on disk"
            if on_disk
            else f"not downloaded yet ({LAYA_DOWNLOAD}, at the next `tempo-server serve`)"
        )
        return Check(
            "Laya",
            "info",
            f"Installed; checkpoint {where}. The runner and its times show here once the "
            "server has loaded it.",
        )
    times = ", ".join(f"{group} {ms:.0f} ms" for group, ms in (status.get("ms") or {}).items())
    when = status.get("updated", "")
    if status.get("status") == "ready":
        return Check(
            "Laya",
            "ok",
            f"Runner {status.get('backend')} ({status.get('runner_note')}); per decision: "
            f"{times}; time limit {status.get('timeout_ms', 0):.0f} ms (measured {when}).",
        )
    return Check(
        "Laya",
        "warn",
        f"Last load ({when}): {status.get('status')}: {status.get('error') or ''}".rstrip(": "),
        "Check the server log; TEMPO_LAYA=off makes the rules decide everything.",
    )


async def run_checks(engine: Engine, port: int = 8000, offline: bool = False) -> list[Check]:
    checks = [check_python(), check_command(), *check_data_dir(engine), *check_keys(engine)]
    if not offline:
        checks += await check_reachable(engine)
    checks.append(await check_ollama(engine))
    checks.append(check_sandbox(engine))
    checks.append(check_laya(engine))
    checks.append(check_port(port))
    return checks


MARKS = {"ok": ("✓", "green"), "warn": ("!", "yellow"), "fail": ("✗", "red"), "info": ("·", "dim")}
