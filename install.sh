#!/bin/sh
# Tempo-server installer for macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/narendrachampaneri/Tempo-server/main/install.sh | sh
#
# Installs uv (or uses pipx or uv if you have one), installs tempo-server as its own isolated
# tool, then runs `tempo-server setup`. No sudo, no virtual environment by hand, no GPU.
#
# Settings (environment variables, all optional):
#   TEMPO_SERVER_INSTALLER   auto (default: uv if present, else pipx if present, else install uv),
#                            uv, or pipx
#   TEMPO_SERVER_SOURCE      what to install (default: the latest source from GitHub; the PyPI
#                            name `tempo-server` once it is published; or a local wheel file)
#   TEMPO_SERVER_PYTHON      Python version uv installs it with (default 3.12; uv downloads it
#                            if needed)
#   TEMPO_SERVER_NO_SETUP    1 to skip `tempo-server setup`
#   TEMPO_SERVER_SETUP_ARGS  arguments for setup when there is no terminal to ask in
#                            (default: --non-interactive)
set -eu

SOURCE="${TEMPO_SERVER_SOURCE:-https://github.com/narendrachampaneri/Tempo-server/archive/refs/heads/main.zip}"
TOOL="${TEMPO_SERVER_INSTALLER:-auto}"
PYTHON_VERSION="${TEMPO_SERVER_PYTHON:-3.12}"

say() { printf '%s\n' "==> $*"; }
fail() { printf '%s\n' "Error: $*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

download() {  # download URL to stdout
    if has curl; then curl -LsSf "$1"
    elif has wget; then wget -qO- "$1"
    else fail "curl or wget is needed to download $1"
    fi
}

if [ "$TOOL" = "auto" ]; then
    if has uv; then TOOL=uv
    elif has pipx; then TOOL=pipx
    else TOOL=uv
    fi
fi

case "$TOOL" in
uv)
    if ! has uv; then
        say "Installing uv (https://docs.astral.sh/uv/), which installs Python tools"
        download https://astral.sh/uv/install.sh | sh
        PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        export PATH
        has uv || fail "uv was installed but is not on PATH; open a new terminal and run this again"
    fi
    say "Installing tempo-server with uv"
    uv tool install --force --python "$PYTHON_VERSION" "$SOURCE"
    uv tool update-shell >/dev/null 2>&1 || true
    BIN_DIR="$(uv tool dir --bin)"
    ;;
pipx)
    PIPX="pipx"
    if ! has pipx; then
        PY=""
        for candidate in python3 python; do
            if has "$candidate"; then PY="$candidate"; break; fi
        done
        [ -n "$PY" ] || fail "Python 3.11+ is needed for pipx (or use TEMPO_SERVER_INSTALLER=uv)"
        say "Installing pipx (https://pipx.pypa.io)"
        "$PY" -m pip install --user --quiet pipx || fail "could not install pipx; try TEMPO_SERVER_INSTALLER=uv"
        PIPX="$PY -m pipx"
    fi
    say "Installing tempo-server with pipx"
    $PIPX install --force "$SOURCE"
    $PIPX ensurepath >/dev/null 2>&1 || true
    BIN_DIR="$($PIPX environment --value PIPX_BIN_DIR 2>/dev/null || echo "$HOME/.local/bin")"
    ;;
*)
    fail "TEMPO_SERVER_INSTALLER must be auto, uv or pipx (got $TOOL)"
    ;;
esac

BIN="$BIN_DIR/tempo-server"
[ -x "$BIN" ] || BIN="$(command -v tempo-server || true)"
[ -n "$BIN" ] || fail "tempo-server was installed but could not be found"
say "Installed $("$BIN" --version)"

if [ "${TEMPO_SERVER_NO_SETUP:-0}" != "1" ]; then
    # Piped into sh, stdin is this script: ask on the terminal itself when there is one.
    if [ -t 1 ] && (exec </dev/tty) 2>/dev/null; then
        "$BIN" setup </dev/tty
    else
        # shellcheck disable=SC2086
        "$BIN" setup ${TEMPO_SERVER_SETUP_ARGS:---non-interactive}
    fi
fi

case ":$PATH:" in
*":$BIN_DIR:"*) ;;
*) say "Open a new terminal so that tempo-server is on your PATH ($BIN_DIR)." ;;
esac
say "Done. Start it with: tempo-server serve   (then open http://127.0.0.1:8000)"
say "Problems? Run: tempo-server doctor"
