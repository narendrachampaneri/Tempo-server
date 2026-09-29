"""Run untrusted Python or JavaScript safely, in WebAssembly (docs/SANDBOX.md).

The runtime is Wasmtime (Bytecode Alliance, ``pip install wasmtime``; wheels for Windows, macOS
and Linux on x86-64 and ARM64). The interpreters are WebAssembly builds:

- Python: CPython 3.14.5 built for WASI by Brett Cannon (a CPython core developer who maintains
  CPython's WASI support), from github.com/brettcannon/cpython-wasi-build. PSF licence.
- JavaScript: QuickJS-ng 0.17.0 (``qjs-wasi.wasm``) from github.com/quickjs-ng/quickjs. MIT.

``tempo-server sandbox install`` downloads them once (checked against the SHA-256 below),
unpacks them in the data folder and compiles them for this machine.

Every run gets a fresh WebAssembly instance and its own empty temporary folder. WASI gives the
code nothing else: no network (no sockets), no processes, no other files (the Python standard
library is mounted read-only). Limits: wall-clock time (Wasmtime epoch interruption), memory
(a cap on the WebAssembly memory), output size and the size of its temporary folder (a
watchdog stops the run the moment either passes its limit). A stopped run is reported with the
reason; nothing it did survives the run.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

Language = Literal["python", "javascript"]
LANGUAGES: tuple[Language, ...] = ("python", "javascript")


@dataclass(frozen=True)
class Runtime:
    language: Language
    version: str
    url: str
    sha256: str
    archive: Literal["zip", "wasm"]
    wasm: str  # the module's path inside the runtime folder
    licence: str
    source: str
    checked: str  # when the URL and checksum were last verified


RUNTIMES: dict[Language, Runtime] = {
    "python": Runtime(
        language="python",
        version="3.14.5",
        url="https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.14.5/"
        "python-3.14.5-wasi_sdk-24.zip",
        sha256="725955278190d9cdafc24e66125143f767e8fa46693e7a9519c9813da9d6e013",
        archive="zip",
        wasm="python.wasm",
        licence="PSF-2.0",
        source="https://github.com/brettcannon/cpython-wasi-build",
        checked="2026-09-29",
    ),
    "javascript": Runtime(
        language="javascript",
        version="quickjs-ng 0.17.0",
        url="https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-wasi.wasm",
        sha256="42a732a676ec2d93488c19411e0fad283bf72658fdad746f089914b523c783b1",
        archive="wasm",
        wasm="qjs-wasi.wasm",
        licence="MIT",
        source="https://github.com/quickjs-ng/quickjs",
        checked="2026-09-29",
    ),
}


@dataclass
class Limits:
    timeout_s: float = 10.0
    memory_mb: int = 256
    output_kb: int = 64  # stdout and stderr together, as read back
    disk_mb: int = 16  # the run's temporary folder, including its output files


@dataclass
class RunResult:
    language: str
    ok: bool  # finished by itself with exit code 0
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    stopped: str | None = None  # "time", "output", "disk" or "trap" (a WebAssembly fault)
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def summary(self) -> str:
        if self.stopped == "time":
            return "stopped: took longer than the time limit"
        if self.stopped == "output":
            return "stopped: printed more than the output limit"
        if self.stopped == "disk":
            return "stopped: wrote more files than the disk limit"
        if self.stopped == "trap":
            return "stopped: WebAssembly fault (stack or memory)"
        return "finished" if self.ok else f"exited with code {self.exit_code}"


class SandboxUnavailable(RuntimeError):
    pass


def sandbox_home(data_dir: Path | None) -> Path:
    """Where the runtimes live: TEMPO_SANDBOX_HOME, else ``sandbox`` in the data folder."""
    chosen = os.environ.get("TEMPO_SANDBOX_HOME", "").strip()
    if chosen:
        return Path(chosen).expanduser()
    if data_dir is not None:
        return data_dir / "sandbox"
    from tempo.paths import default_data_dir

    return default_data_dir() / "sandbox"


def wasmtime_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("wasmtime")
    except Exception:  # not installed
        return None


class Sandbox:
    """Runs code in a fresh WebAssembly instance per call. Thread-safe; blocking (use
    ``asyncio.to_thread``); at most ``parallel`` runs at a time."""

    def __init__(self, home: Path, limits: Limits | None = None, parallel: int = 2) -> None:
        self.home = home
        self.limits = limits or Limits()
        self._slots = threading.BoundedSemaphore(parallel)
        self._compile_lock = threading.Lock()

    # --- installing -----------------------------------------------------------------------

    def folder(self, language: Language) -> Path:
        rt = RUNTIMES[language]
        return self.home / f"{language}-{rt.version.replace(' ', '-')}"

    def installed(self, language: Language) -> bool:
        return (self.folder(language) / RUNTIMES[language].wasm).is_file()

    def available(self, language: Language) -> bool:
        return wasmtime_version() is not None and self.installed(language)

    def install(
        self,
        language: Language,
        say: Callable[[str], None] = lambda _: None,
        opener: Callable[[str], Any] | None = None,
    ) -> Path:
        """Download, check the SHA-256, unpack and compile one runtime (once)."""
        rt = RUNTIMES[language]
        target = self.folder(language)
        if self.installed(language):
            self._compiled(language)
            return target
        self.home.mkdir(parents=True, exist_ok=True)
        say(f"Downloading {language} {rt.version} ({rt.url})")
        opener = opener or (lambda url: urllib.request.urlopen(url, timeout=120))  # noqa: S310
        with opener(rt.url) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != rt.sha256:
            raise SandboxUnavailable(
                f"{language} runtime checksum mismatch (got {digest[:16]}…, expected "
                f"{rt.sha256[:16]}…); nothing was installed"
            )
        staging = Path(tempfile.mkdtemp(prefix=f".{language}-", dir=self.home))
        try:
            if rt.archive == "zip":
                archive = staging / "runtime.zip"
                archive.write_bytes(data)
                with zipfile.ZipFile(archive) as zf:
                    for member in zf.namelist():  # never write outside the folder
                        resolved = (staging / member).resolve()
                        if not str(resolved).startswith(str(staging.resolve())):
                            raise SandboxUnavailable(f"unsafe path in archive: {member}")
                    zf.extractall(staging)
                archive.unlink()
            else:
                (staging / rt.wasm).write_bytes(data)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        say(f"Compiling {language} for this computer (once; this can take a minute)")
        self._compiled(language)
        say(f"{language} sandbox ready")
        return target

    # --- compiling --------------------------------------------------------------------------

    def _engine(self) -> Any:
        import wasmtime

        config = wasmtime.Config()
        config.epoch_interruption = True
        config.max_wasm_stack = 1 << 20
        return wasmtime.Engine(config)

    def _compiled(self, language: Language) -> Path:
        """The module compiled for this machine and Wasmtime version (compiled once, then
        loaded in milliseconds)."""
        import wasmtime

        rt = RUNTIMES[language]
        cache = self.folder(language) / f"{rt.wasm}.wasmtime-{wasmtime_version()}.cwasm"
        with self._compile_lock:
            if not cache.exists():
                module = wasmtime.Module.from_file(self._engine(), self.folder(language) / rt.wasm)
                partial = cache.with_suffix(".partial")
                partial.write_bytes(module.serialize())
                os.replace(partial, cache)
        return cache

    # --- running ----------------------------------------------------------------------------

    def run(
        self,
        language: Language,
        code: str,
        files: dict[str, str] | None = None,
        limits: Limits | None = None,
    ) -> RunResult:
        """Run ``code`` (a whole program) and return what happened. ``files``: extra files
        placed next to it in its temporary folder."""
        if not self.available(language):
            raise SandboxUnavailable(
                f"The {language} sandbox is not installed: run `tempo-server sandbox install`"
            )
        limits = limits or self.limits
        compiled = self._compiled(language)
        with self._slots:
            return self._run(language, code, files or {}, limits, compiled)

    def _run(
        self,
        language: Language,
        code: str,
        files: dict[str, str],
        limits: Limits,
        compiled: Path,
    ) -> RunResult:
        import wasmtime

        engine = self._engine()  # one engine per run: interrupting it touches no other run
        module = wasmtime.Module.deserialize_file(engine, str(compiled))
        work = Path(tempfile.mkdtemp(prefix="tempo-sandbox-"))
        io = Path(tempfile.mkdtemp(prefix="tempo-sandbox-io-"))  # not visible to the code
        stopped: list[str] = []
        done = threading.Event()
        started = time.perf_counter()
        try:
            for name, text in files.items():
                (work / Path(name).name).write_text(text, encoding="utf-8")
            wasi = wasmtime.WasiConfig()
            if language == "python":
                (work / "main.py").write_text(code, encoding="utf-8")
                # -E -s: ignore PYTHON* variables and user site; /work stays importable
                wasi.argv = ["python", "-E", "-s", "-X", "utf8", "/work/main.py"]
                wasi.env = [("PYTHONHOME", "/"), ("PYTHONDONTWRITEBYTECODE", "1")]
                wasi.preopen_dir(str(self.folder(language) / "lib"), "/lib", False)
            else:
                (work / "main.js").write_text(code, encoding="utf-8")
                wasi.argv = ["qjs", "/work/main.js"]  # no --std: console only, no std/os
            wasi.preopen_dir(str(work), "/work", True)
            out_path, err_path = io / "stdout", io / "stderr"
            wasi.stdout_file = str(out_path)
            wasi.stderr_file = str(err_path)

            store = wasmtime.Store(engine)
            store.set_limits(memory_size=limits.memory_mb * 1024 * 1024)
            store.set_wasi(wasi)
            store.set_epoch_deadline(1)

            def stop(reason: str) -> None:
                if not stopped:
                    stopped.append(reason)
                engine.increment_epoch()

            def watchdog() -> None:
                deadline = started + limits.timeout_s
                while not done.wait(0.02):
                    if time.perf_counter() >= deadline:
                        stop("time")
                        return
                    if _size(out_path) + _size(err_path) > limits.output_kb * 1024 * 4:
                        stop("output")
                        return
                    if _tree_size(work) > limits.disk_mb * 1024 * 1024:
                        stop("disk")
                        return

            guard = threading.Thread(target=watchdog, name="tempo-sandbox-watchdog", daemon=True)
            guard.start()
            linker = wasmtime.Linker(engine)
            linker.define_wasi()
            exit_code: int | None = None
            try:
                instance = linker.instantiate(store, module)
                instance.exports(store)["_start"](store)
                exit_code = 0
            except wasmtime.ExitTrap as exc:
                exit_code = exc.code
            except wasmtime.Trap as exc:
                if not stopped:
                    stopped.append("trap")
                log.debug("sandbox trap: %s", str(exc).splitlines()[0])
            except wasmtime.WasmtimeError as exc:
                if not stopped:
                    stopped.append("trap")
                log.debug("sandbox error: %s", exc)
            finally:
                done.set()
                guard.join(timeout=1)
            stdout, t1 = _read(out_path, limits.output_kb * 1024)
            stderr, t2 = _read(err_path, limits.output_kb * 1024)
            return RunResult(
                language=language,
                ok=exit_code == 0 and not stopped,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=round((time.perf_counter() - started) * 1000),
                stopped=stopped[0] if stopped else None,
                truncated=t1 or t2,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(io, ignore_errors=True)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tree_size(folder: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(folder):
        for name in names:
            total += _size(Path(root) / name)
    return total


def _read(path: Path, limit: int) -> tuple[str, bool]:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return "", False
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


@dataclass
class SandboxStatus:
    enabled: bool
    wasmtime: str | None
    home: str
    languages: dict[str, bool] = field(default_factory=dict)
