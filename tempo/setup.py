"""`tempo setup`: walk a new user through each free provider.

For each provider it shows where to get a key, the free limits (with their source and date), the
terms (may outputs train other models, what the free tier does with prompts) and any rule Tempo
applies. It checks each key with the provider, stores it encrypted in the vault (only for this
installation's owner), writes non-secret settings to ``<data dir>/settings.env``, reads the live
model lists, and ends with how many free requests a day the user now has.

Provider rules applied here (CLAUDE.md, owner's decisions): Cerebras stays off (trial credits
need a payment method); NVIDIA only for the owner's own private testing; Cohere and OpenCode Zen
only with the user's own key. Keys are never printed: only a fingerprint.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import typer
from rich.console import Console
from rich.table import Table

from tempo import budget
from tempo.accounts import LOCAL_USER, fingerprint
from tempo.config import SETTINGS_FILE
from tempo.registry import OLLAMA_DEFAULT_BASE
from tempo.sync import RegistrySync, verify_key

if TYPE_CHECKING:
    from tempo.engine import Engine
    from tempo.types import ProviderInfo

# Local first, then the free tiers with the most requests, then the opt-in ones.
ORDER = [
    "ollama",
    "groq",
    "gemini",
    "openrouter",
    "cloudflare",
    "mistral",
    "cohere",
    "opencode",
    "nvidia",
    "cerebras",
]
POLICY_TEXT = {
    "ok": "does not train on or keep your prompts",
    "may-log": "may log your prompts",
    "may-train": "may use your prompts to train its models: don't send private data "
    "(Tempo skips it for --no-logging and private questions)",
    "unknown": "not stated by the provider",
}
TRAINING_TEXT = {
    "yes": "yes (Tempo may keep its answers as training data)",
    "no": "no (never used as training data)",
    "unclear": "unclear (kept out of training data until the provider confirms)",
}
# Opt-in providers: what the user must accept before Tempo turns them on.
OPT_IN = {
    "nvidia": "Use NVIDIA only for your own private testing? Its trial terms allow internal "
    "testing and evaluation only; other users of this server can never use it",
    "opencode": "Turn on OpenCode Zen with your own Zen key? Free models only; kept out of "
    "collect, exports and eval",
}
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")


# --- settings.env -------------------------------------------------------------------------


def settings_path(engine: Engine) -> Path | None:
    data_dir = engine.settings.data_dir
    return data_dir / SETTINGS_FILE if data_dir is not None else None


def read_settings(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip().strip('"')
    return values


def write_settings(path: Path | None, updates: dict[str, str | None]) -> None:
    """Merge non-secret settings into settings.env (None removes one) and apply them now."""
    for name in updates:
        if _SECRET_NAME.search(name):
            raise ValueError(f"{name} looks like a secret; keys go in the vault, not settings.env")
    for name, value in updates.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if path is None:
        return
    values = read_settings(path)
    for name, value in updates.items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Written by `tempo setup`. Non-secret settings only: keys live in the vault."]
    lines += [f"{name}={value}" for name, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def enable_list(current: str, provider: str) -> str:
    names = [p.strip() for p in current.split(",") if p.strip()]
    if provider not in names:
        names.append(provider)
    return ",".join(names)


# --- what to show for each provider -------------------------------------------------------


def limits_text(engine: Engine, provider: ProviderInfo) -> str:
    per_day, per_minute, note = budget.daily_capacity(engine.registry, provider.id)
    parts = []
    if per_day:
        parts.append(f"about {per_day:,} requests a day")
    if per_minute:
        parts.append(f"{per_minute} a minute")
    text = ", ".join(parts) or note or provider.free_limit_note or "not published"
    if parts and note:
        text += f" ({note})"
    return text


def describe(engine: Engine, provider: ProviderInfo) -> list[str]:
    lines = []
    if provider.signup_url:
        verb = "Download" if provider.local else "Get a free key"
        lines.append(f"{verb}: {provider.signup_url}")
    if not provider.local:
        tier = "trial credits" if provider.free_tier == "trial" else "free tier"
        lines.append(f"Free limits ({tier}): {limits_text(engine, provider)}")
        if provider.limits_source:
            lines.append(f"  source: {provider.limits_source}, checked {provider.limits_checked}")
        if provider.free_tier_note:
            lines.append(f"  {provider.free_tier_note}")
        verdict = "licence decides" if provider.licence_decides else provider.training_on_outputs
        lines.append(
            "Training on its answers: "
            + (
                "each model's licence decides (Apache-2.0 or MIT: yes)"
                if verdict == "licence decides"
                else TRAINING_TEXT[provider.training_on_outputs]
            )
        )
        lines.append(f"Data policy: {POLICY_TEXT.get(provider.data_policy, provider.data_policy)}")
    if provider.blocked_for:
        lines.append(f"Never used for: {', '.join(provider.blocked_for)}")
    return lines


async def probe_ollama(base: str, transport: httpx.AsyncBaseTransport | None = None) -> int | None:
    """Number of models installed in a running Ollama, or None when it is not running."""
    try:
        async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
            response = await client.get(f"{base.rstrip('/')}/api/tags")
            response.raise_for_status()
            return len(response.json().get("models") or [])
    except (httpx.HTTPError, ValueError):
        return None


# --- the wizard ---------------------------------------------------------------------------


class Wizard:
    def __init__(self, engine: Engine, console: Console, sync: bool = True) -> None:
        self.engine = engine
        self.console = console
        self.sync = sync
        self.settings_file = settings_path(engine)
        self.stored: list[str] = []

    def say(self, text: str = "", style: str | None = None) -> None:
        self.console.print(text, style=style, markup=False, highlight=False)

    def run(self, only: set[str] | None = None) -> None:
        registry = self.engine.registry
        self.say("Tempo-server setup", style="bold")
        self.say(
            "Each provider below has a free tier. Paste your own key, or press Enter to skip. "
            "Keys are checked with the provider, stored encrypted, and never shown again "
            "(only a fingerprint)."
        )
        if self.settings_file is None:
            self.say("TEMPO_DATA_DIR=memory: nothing is saved after this run.", style="yellow")
        for pid in ORDER:
            provider = registry.providers.get(pid)
            if provider is None or (only and pid not in only):
                continue
            self.say()
            self.say(f"── {provider.label} ──", style="bold")
            for line in describe(self.engine, provider):
                self.say(line)
            if provider.local:
                self.ollama(provider)
            elif pid == "cerebras":
                self.say(
                    "Off: "
                    + (provider.disabled_note or "trial credits need a payment method.")
                    + " Tempo does not ask for a Cerebras key until the owner decides.",
                    style="yellow",
                )
            else:
                self.keyed(provider)
        self.finish()

    def ollama(self, provider: ProviderInfo) -> None:
        env_name = provider.base_env or "OLLAMA_API_BASE"
        base = os.environ.get(env_name, "").strip() or OLLAMA_DEFAULT_BASE
        count = asyncio.run(probe_ollama(base))
        if count is None:
            self.say(
                f"Ollama is not running at {base}. Optional: install it, then run "
                "`ollama pull qwen3:1.7b` and `tempo setup --only ollama` again. Local models "
                "answer simple questions (saving free quota) and take over when every free "
                "quota is used up."
            )
            return
        self.say(f"Ollama is running at {base} with {count} model(s).", style="green")
        write_settings(self.settings_file, {env_name: base})
        if not count:
            self.say("Pull a small model for local answers: ollama pull qwen3:1.7b")

    def keyed(self, provider: ProviderInfo) -> None:
        registry = self.engine.registry
        pid = provider.id
        question = OPT_IN.get(pid)
        if question is not None and not registry.is_enabled(pid):
            if not typer.confirm(question, default=False):
                self.say("Skipped (stays off).")
                return
        if provider.byok_only:
            self.say("Uses only your own key, never a server-wide one.")
        if "{CLOUDFLARE_ACCOUNT_ID}" in (provider.openai_base or ""):
            current = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
            account = typer.prompt(
                "Cloudflare account id (not a secret; Enter to skip)",
                default=current,
                show_default=bool(current),
            ).strip()
            if not account:
                self.say("Skipped (Cloudflare needs your account id).")
                return
            write_settings(self.settings_file, {"CLOUDFLARE_ACCOUNT_ID": account})
        existing = self.engine.accounts.keys(LOCAL_USER).get(pid)
        if existing and not typer.confirm(
            f"A key is already stored ({fingerprint(existing)}). Replace it?", default=False
        ):
            self.enable(provider)
            return
        api_key = typer.prompt(
            f"{provider.label} key (hidden; Enter to skip)",
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        if not api_key:
            self.say("Skipped.")
            return
        verified = asyncio.run(verify_key(registry, pid, api_key))
        if verified is False:
            self.say(f"{provider.label} rejected this key; nothing stored.", style="red")
            return
        self.engine.accounts.set_key(LOCAL_USER, pid, api_key, verified)
        self.enable(provider)
        state = "verified" if verified else "stored (could not verify now)"
        self.say(f"{provider.label} key {fingerprint(api_key)} {state}.", style="green")
        self.stored.append(pid)

    def enable(self, provider: ProviderInfo) -> None:
        if provider.enabled:
            return
        current = os.environ.get("TEMPO_ENABLE_PROVIDERS", "")
        write_settings(
            self.settings_file, {"TEMPO_ENABLE_PROVIDERS": enable_list(current, provider.id)}
        )

    def finish(self) -> None:
        engine = self.engine
        if self.sync:
            self.say()
            self.say("Reading each provider's live model list…")
            try:
                status = asyncio.run(
                    RegistrySync(engine.registry, engine.health).run(engine.listing_keys())
                )
                engine.save_catalog(status)
                failed = [s.provider for s in status.values() if not s.ok]
                if failed:
                    self.say(f"Could not read: {', '.join(failed)} (seed lists used).")
            except Exception as exc:  # the summary still works from the seed lists
                self.say(f"Model lists not refreshed: {exc}", style="yellow")
        asyncio.run(engine.registry.discover_ollama())
        summary(engine, self.console)


def summary(engine: Engine, console: Console) -> int:
    """Print the free requests a day per configured provider; returns the total."""
    view = budget.quota_view(engine, engine.access_for(LOCAL_USER))
    table = Table(title="Your free requests a day", header_style="bold")
    for column in ("provider", "requests a day", "per minute", "note"):
        table.add_column(column)
    total = 0
    for q in view:
        if q.local:
            table.add_row(q.label, "no limit (local)", "-", "")
            continue
        total += q.per_day or 0
        table.add_row(
            q.label,
            f"{q.per_day:,}" if q.per_day else "-",
            str(q.per_minute or "-"),
            q.note,
        )
    console.print(table)

    def say(text: str) -> None:
        console.print(text, markup=False, highlight=False)

    if not view:
        say("No provider is set up yet. Run `tempo setup` again with a free key, or start Ollama.")
    else:
        say(f"In total about {total:,} free requests a day, plus any local models.")
        say(
            "Next: `tempo-server serve`, then open http://127.0.0.1:8000 or connect an app "
            "(docs/CONNECT.md)."
        )
    return total
