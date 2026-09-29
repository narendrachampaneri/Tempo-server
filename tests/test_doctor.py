"""`tempo-server doctor`: each check, and a fix for each problem."""

import socket

import httpx
from conftest import make_engine
from typer.testing import CliRunner

from tempo import doctor


def test_python_version():
    assert doctor.check_python((3, 10, 9)).status == "fail"
    assert (
        "3.11" in doctor.check_python((3, 10, 9)).fix
        or "3.12" in doctor.check_python((3, 10, 9)).fix
    )
    assert doctor.check_python((3, 13, 1)).status == "ok"


def test_data_folder(tmp_path, _private_home):
    engine, _ = make_engine(data_dir=tmp_path)
    assert [c.status for c in doctor.check_data_dir(engine)] == ["ok"]
    (_private_home / ".tempo").mkdir()
    checks = doctor.check_data_dir(engine)
    assert checks[-1].status == "warn" and "delete" in checks[-1].fix
    memory, _ = make_engine()
    assert doctor.check_data_dir(memory)[0].status == "warn"


def test_keys(monkeypatch):
    engine, _ = make_engine(env={})  # no keys
    assert doctor.check_keys(engine)[0].status == "warn"
    engine, _ = make_engine()
    first = doctor.check_keys(engine)[0]
    assert first.status == "ok" and "Alpha" in first.message


async def test_reachability_uses_no_key():
    engine, _ = make_engine(env={})
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(401)  # any answer means the host is reachable

    checks = await doctor.check_reachable(engine, httpx.MockTransport(handler))
    assert [c.status for c in checks] == ["ok"] and "authorization" not in seen[0].headers

    def down(request):
        raise httpx.ConnectError("no route")

    checks = await doctor.check_reachable(engine, httpx.MockTransport(down))
    assert checks[0].status == "fail" and "HTTPS_PROXY" in checks[0].fix


async def test_ollama_states(monkeypatch):
    engine, _ = make_engine()
    monkeypatch.delenv("OLLAMA_API_BASE", raising=False)

    def down(request):
        raise httpx.ConnectError("refused")

    assert (await doctor.check_ollama(engine, httpx.MockTransport(down))).status == "info"
    tags = httpx.MockTransport(lambda r: httpx.Response(200, json={"models": [{"name": "q"}]}))
    assert (await doctor.check_ollama(engine, tags)).status == "warn"  # running, not set up
    monkeypatch.setenv("OLLAMA_API_BASE", "http://localhost:11434")
    assert (await doctor.check_ollama(engine, tags)).status == "ok"
    empty = httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []}))
    assert "ollama pull" in (await doctor.check_ollama(engine, empty)).fix


def test_port():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        check = doctor.check_port(port)
        assert check.status == "fail" and f"--port {port + 1}" in check.fix
    assert doctor.check_port(port).status == "ok"


def test_cli_doctor_offline(monkeypatch, tmp_path):
    from tempo.cli import app

    monkeypatch.setenv("TEMPO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TEMPO_EMBEDDINGS", "off")
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:9")  # nothing listens there
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = CliRunner().invoke(app, ["doctor", "--offline", "--port", str(port)])
    assert result.exit_code == 0, result.output
    assert "✓ Python" in result.output and "Data folder" in result.output
    assert "fix: Run `tempo-server setup`" in result.output
