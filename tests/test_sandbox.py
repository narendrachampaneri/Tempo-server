"""The WebAssembly sandbox: every escape and exhaustion attempt must be stopped cleanly.

Needs the runtimes (`tempo-server sandbox install`, found through TEMPO_SANDBOX_HOME). CI
installs them and sets TEMPO_SANDBOX_REQUIRED=1, so there a missing runtime is a failure,
not a skip."""

import hashlib
import io
import os
import tempfile
import threading
import zipfile
from pathlib import Path

import pytest

from tempo.sandbox import RUNTIMES, Limits, Sandbox, SandboxUnavailable, sandbox_home

LIMITS = Limits(timeout_s=3, memory_mb=128, output_kb=16, disk_mb=4)


@pytest.fixture(scope="module")
def sandbox():
    home = sandbox_home(None)
    box = Sandbox(home, LIMITS)
    if not (box.available("python") and box.available("javascript")):
        if os.environ.get("TEMPO_SANDBOX_REQUIRED") == "1":
            pytest.fail(f"sandbox runtimes are not installed in {home}")
        pytest.skip("sandbox runtimes not installed (tempo-server sandbox install)")
    return box


def py(sandbox, code, **kw):
    return sandbox.run("python", code, **kw)


def js(sandbox, code, **kw):
    return sandbox.run("javascript", code, **kw)


def test_runs_python_and_javascript(sandbox):
    r = py(sandbox, "import json, math, fractions\nprint(json.dumps({'x': math.sqrt(16)}))")
    assert r.ok and r.stdout.strip() == '{"x": 4.0}' and r.stopped is None
    r = js(sandbox, "console.log([1,2,3].map(x => x * 2).join(','))")
    assert r.ok and r.stdout.strip() == "2,4,6"


# --- stopped cleanly --------------------------------------------------------------------


@pytest.mark.parametrize("run,code", [(py, "while True:\n    pass"), (js, "for(;;){}")])
def test_infinite_loop_is_stopped_at_the_time_limit(sandbox, run, code):
    r = run(sandbox, code)
    assert not r.ok and r.stopped == "time"
    assert 2800 <= r.duration_ms < 8000


@pytest.mark.parametrize(
    "run,code",
    [(py, "while True:\n    print('x' * 10000)"), (js, "for(;;) console.log('x'.repeat(10000))")],
)
def test_huge_output_is_stopped_and_truncated(sandbox, run, code):
    r = run(sandbox, code)
    assert not r.ok and r.stopped == "output" and r.truncated
    assert len(r.stdout) <= LIMITS.output_kb * 1024
    assert r.duration_ms < 2500  # stopped by the watchdog, not by the clock


@pytest.mark.parametrize(
    "run,code,error",
    [
        (py, "x = []\nwhile True:\n    x.append(bytearray(10**6))", "MemoryError"),
        (py, "x = bytearray(512 * 1024 * 1024)", "MemoryError"),
        (js, "const a = [];\nfor(;;) a.push(new Array(1e6).fill(1))", "out of memory"),
    ],
)
def test_memory_bombs_hit_the_memory_cap(sandbox, run, code, error):
    r = run(sandbox, code)
    assert not r.ok
    assert error in r.stderr or r.stopped in ("trap", "time")


def test_filling_the_disk_is_stopped(sandbox):
    r = py(
        sandbox,
        "with open('/work/big', 'wb') as f:\n    while True:\n        f.write(b'x' * 10**6)",
    )
    assert not r.ok and r.stopped == "disk"


def test_deep_recursion_is_stopped(sandbox):
    r = py(
        sandbox, "import sys\nsys.setrecursionlimit(10**7)\ndef f(n):\n    return f(n + 1)\nf(0)"
    )
    assert not r.ok and (r.stopped in ("trap", "time") or "RecursionError" in r.stderr)


# --- no way out -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "/", "/work/../../etc/hosts", "C:/Windows/win.ini", "../../secret"]
)
def test_files_outside_its_folder_cannot_be_read(sandbox, path):
    r = py(
        sandbox,
        f"import os\np = {path!r}\nprint(open(p).read() if os.path.isfile(p) else os.listdir(p))",
    )
    assert not r.ok
    assert any(
        e in r.stderr for e in ("FileNotFoundError", "PermissionError", "NotADirectoryError")
    )


def test_the_hosts_real_files_are_invisible(sandbox, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    r = py(sandbox, f"print(open({str(secret)!r}).read())")
    assert not r.ok and "TOP SECRET" not in r.stdout + r.stderr
    r = js(sandbox, "console.log(typeof std, typeof os, typeof require)")
    assert r.stdout.strip() == "undefined undefined undefined"


def test_the_standard_library_is_read_only(sandbox):
    r = py(sandbox, "open('/lib/python3.14/os.py', 'a').write('# changed')")
    assert not r.ok and "PermissionError" in r.stderr


@pytest.mark.parametrize(
    "code",
    [
        "import socket\ns = socket.socket()\ns.connect(('1.1.1.1', 80))",
        "import socket\nsocket.create_connection(('example.com', 80), timeout=2)",
        "import urllib.request\nurllib.request.urlopen('http://example.com', timeout=2)",
    ],
)
def test_no_network(sandbox, code):
    r = py(sandbox, code)
    assert not r.ok and r.stopped is None
    assert any(
        e in r.stderr
        for e in ("Not supported", "OSError", "URLError", "has no attribute 'getaddrinfo'")
    )


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nsubprocess.run(['ls'])",
        "import os\nos.system('ls')",
        "import os\nos.fork()",
        "import os\nos.execv('/bin/sh', ['sh'])",
    ],
)
def test_no_processes(sandbox, code):
    r = py(sandbox, code)
    assert not r.ok and r.stopped is None
    assert any(e in r.stderr for e in ("does not support processes", "AttributeError", "OSError"))


def test_no_host_environment_variables(sandbox):
    os.environ["TEMPO_TEST_SECRET"] = "placeholder-secret"
    try:
        r = py(sandbox, "import os\nprint(sorted(os.environ))")
    finally:
        del os.environ["TEMPO_TEST_SECRET"]
    assert r.ok and "TEMPO_TEST_SECRET" not in r.stdout and "PATH" not in r.stdout


def test_each_run_is_fresh_and_leaves_nothing(sandbox):
    before = set(Path(tempfile.gettempdir()).glob("tempo-sandbox-*"))
    assert py(sandbox, "open('/work/note.txt', 'w').write('hi')").ok
    r = py(sandbox, "import os\nprint(os.listdir('/work'))")
    assert r.ok and "note.txt" not in r.stdout
    assert set(Path(tempfile.gettempdir()).glob("tempo-sandbox-*")) == before


def test_parallel_runs_do_not_interfere(sandbox):
    results = {}

    def go(name, code):
        results[name] = py(sandbox, code)

    threads = [
        threading.Thread(target=go, args=("loop", "while True:\n    pass")),
        threading.Thread(target=go, args=("quick", "print(21 * 2)")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results["quick"].ok and results["quick"].stdout.strip() == "42"
    assert results["loop"].stopped == "time"


# --- installing ------------------------------------------------------------------------


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_install_refuses_a_download_with_the_wrong_checksum(tmp_path):
    box = Sandbox(tmp_path)
    with pytest.raises(SandboxUnavailable, match="checksum"):
        box.install("javascript", opener=lambda url: _Response(b"not the real runtime"))
    assert not box.installed("javascript") and list(tmp_path.iterdir()) == []


def test_install_refuses_an_archive_that_writes_outside_its_folder(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escaped.txt", "x")
        zf.writestr("python.wasm", "x")
    data = buf.getvalue()
    runtime = RUNTIMES["python"]
    monkeypatch.setitem(
        RUNTIMES,
        "python",
        type(runtime)(**{**runtime.__dict__, "sha256": hashlib.sha256(data).hexdigest()}),
    )
    box = Sandbox(tmp_path / "home")
    with pytest.raises(SandboxUnavailable, match="unsafe path"):
        box.install("python", opener=lambda url: _Response(data))
    assert not (tmp_path / "escaped.txt").exists() and not box.installed("python")


def test_not_installed_is_a_clear_error(tmp_path):
    with pytest.raises(SandboxUnavailable, match="tempo-server sandbox install"):
        Sandbox(tmp_path).run("python", "print(1)")
