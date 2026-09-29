"""Where Tempo-server keeps its data on each system, and the one-time move from ``~/.tempo``.

- Windows: ``%LOCALAPPDATA%\\tempo-server``
- macOS: ``~/Library/Application Support/tempo-server``
- Linux and others: ``$XDG_DATA_HOME/tempo-server`` (default ``~/.local/share/tempo-server``)

``TEMPO_DATA_DIR`` overrides it (``memory`` keeps nothing). Versions before 0.1 used
``~/.tempo``; the first run that uses the default folder moves it there (see ``migrate``).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "tempo-server"
LEGACY_NAME = ".tempo"

log = logging.getLogger(__name__)

# What the last migrate() did, for the CLI to show once.
notices: list[str] = []


def default_data_dir(
    env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> Path:
    env = os.environ if env is None else env
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    base = env.get("XDG_DATA_HOME", "").strip()
    return (Path(base) if base else home / ".local" / "share") / APP_NAME


def legacy_data_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / LEGACY_NAME


def _has_files(folder: Path) -> bool:
    return folder.is_dir() and any(folder.iterdir())


def migrate(target: Path, legacy: Path | None = None) -> str | None:
    """Move an old ``~/.tempo`` folder to ``target`` once, safely.

    Nothing happens when there is no old folder, or when ``target`` already has files (then the
    old folder is left alone and a notice says so). The move is a rename when both are on the
    same drive; otherwise the folder is copied to a temporary name next to ``target``, renamed
    into place, and only then is the old folder removed, so a failure never loses data.
    """
    legacy = legacy or legacy_data_dir()
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return None
    if _has_files(target):
        note = (
            f"Old Tempo data folder {legacy} left as is: {target} is already in use. "
            f"Delete {legacy} once you no longer need it."
        )
        notices.append(note)
        return note
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rmdir()  # empty
    try:
        os.replace(legacy, target)
    except OSError:  # another drive or file system: copy, then swap in, then delete
        staging = target.with_name(target.name + ".moving")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(legacy, staging, copy_function=shutil.copy2)
        os.replace(staging, target)
        shutil.rmtree(legacy, ignore_errors=True)
    note = f"Moved your Tempo data from {legacy} to {target}."
    log.info(note)
    notices.append(note)
    return note


def resolve_data_dir(env: Mapping[str, str]) -> Path | None:
    """The data folder to use: TEMPO_DATA_DIR, else the system default (after the move)."""
    chosen = env.get("TEMPO_DATA_DIR", "").strip()
    if chosen.lower() == "memory":
        return None
    if chosen:
        return Path(chosen).expanduser()
    target = default_data_dir(env)
    try:
        migrate(target)
    except OSError as exc:  # never block a run; the old folder stays where it was
        notices.append(f"Could not move {legacy_data_dir()} to {target}: {exc}")
    return target
