"""The MCP server, tested with the official MCP SDK's own client: in-process, over Streamable
HTTP (a Tempo key is required) and over stdio (the command desktop apps run)."""

import json
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager

import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from test_real_client import demo_settings

from tempo.engine import Engine
from tempo.mcp_server import build_server, http_app

TOOLS = {"ask", "second_opinion", "verify", "models", "quota"}


def data(result):
    """A tool's JSON result (structured content, else the text block)."""
    assert not result.is_error, result
    if result.structured_content is not None:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


@asynccontextmanager
async def connect():
    """The MCP SDK's in-process client on a demo-mode server (entered in the test's own task:
    anyio cancel scopes can't cross pytest's fixture tasks)."""
    engine = Engine.from_settings(demo_settings())
    await engine.startup(oneshot=True)
    async with Client(build_server(engine)) as c:
        yield c


async def test_lists_the_five_tools_with_descriptions():
    async with connect() as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == TOOLS
        assert "judge from another model family" in tools["ask"].description
        assert tools["models"].annotations.read_only_hint
        ask = tools["ask"].input_schema
        assert ask["required"] == ["question"] and set(ask["properties"]) >= {"mode", "private"}


async def test_ask_returns_answer_models_and_checks():
    async with connect() as client:
        out = data(await client.call_tool("ask", {"question": "What is 17% of 2,340?"}))
        assert out["answer"] and out["model"].startswith("mock/")
        assert out["models_used"] and out["checks"] and "passed" in out["checks"][0]
        assert out["stop_reason"] == "passed"
        fast = data(
            await client.call_tool("ask", {"question": "hi", "mode": "fast", "private": True})
        )
        assert fast["answer"]


async def test_local_only_with_no_local_model_says_why():
    async with connect() as client:
        out = data(await client.call_tool("ask", {"question": "hi", "local_only": True}))
        assert out["error"] and not out["answer"]


async def test_second_opinion_uses_two_families_and_compares():
    async with connect() as client:
        out = data(await client.call_tool("second_opinion", {"question": "Is Python typed?"}))
        assert out["first"]["family"] != out["second"]["family"]
        assert out["comparison"]["verdict"] in ("agree", "partly", "disagree")
        assert out["comparison"]["compared_by"]


async def test_verify_uses_a_judge_from_another_family():
    async with connect() as client:
        out = data(
            await client.call_tool(
                "verify", {"question": "What is 2+2?", "answer": "5", "answer_model": "mock/smart"}
            )
        )
        assert out["verdict"] in ("pass", "fail") and out["judge_family"] != "smart"
        assert isinstance(out["problems"], list) and "quick_checks" in out


async def test_models_and_quota():
    async with connect() as client:
        models = data(await client.call_tool("models", {}))["models"]
        assert models and {"model", "health", "ready", "limits"} <= set(models[0])
        quota = data(await client.call_tool("quota", {}))
        assert "providers" in quota and quota["all_used_up"] is False


@pytest.fixture
def http_server():
    settings = demo_settings(api_key="placeholder-admin-key")
    engine = Engine.from_settings(settings)
    _, user_key = engine.accounts.create_user("asha")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        http_app(engine, settings.api_key), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp", user_key
    server.should_exit = True
    thread.join(timeout=5)


def http_client(url, key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    http = create_mcp_http_client(headers=headers)
    http._trust_env = False  # straight to localhost, never through a proxy
    return Client(streamable_http_client(url, http_client=http))


async def test_http_needs_a_tempo_key(http_server):
    url, user_key = http_server
    async with http_client(url, user_key) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS
        out = data(await c.call_tool("ask", {"question": "hi"}))
        assert out["answer"]
    async with http_client(url, "placeholder-admin-key") as c:
        assert data(await c.call_tool("quota", {}))["all_used_up"] is False
    async with httpx2.AsyncClient(trust_env=False) as raw:
        for headers in ({}, {"Authorization": "Bearer wrong-key"}):
            r = await raw.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1})
            assert r.status_code == 401 and "Tempo API key" in r.text


async def test_stdio_command(tmp_path):
    env = {
        **os.environ,
        "TEMPO_ENABLE_MOCK": "1",
        "TEMPO_DATA_DIR": "memory",
        "TEMPO_EMBEDDINGS": "off",
        "TEMPO_LAYA": "off",
        "TEMPO_SYNC_INTERVAL": "0",
        "PYTHONUTF8": "1",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tempo.cli", "mcp"], env=env, cwd=str(tmp_path)
    )
    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS
        out = data(await c.call_tool("ask", {"question": "What is 2+2?"}))
        assert out["answer"] and out["model"].startswith("mock/")


def test_every_config_snippet_in_the_docs_is_valid_json():
    import re
    from pathlib import Path

    text = (Path(__file__).parent.parent / "docs" / "MCP.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, re.S)
    assert len(blocks) >= 8
    for block in blocks:
        config = json.loads(block)
        servers = config.get("mcpServers") or config.get("servers")
        entry = servers["tempo-server"]
        assert entry.get("args") == ["mcp"] or entry.get("url", "").endswith("/mcp")
        assert "Bearer placeholder" not in block and "gsk_" not in block


async def test_mcp_questions_are_logged_apart_and_kept_out_of_training_exports():
    from tempo import sft, tuning
    from tempo.config import Settings

    engine = Engine.from_settings(demo_settings())
    await engine.startup(oneshot=True)
    async with Client(build_server(engine)) as c:
        data(await c.call_tool("ask", {"question": "Review my private code: x = 1"}))
    sources = {q["source"] for q in engine.store.query("SELECT source FROM questions")}
    assert sources == {"mcp"}
    users = {"local", "admin", "collect"}
    *_, stats = sft.build(engine.store, engine.registry, users=users)
    assert stats.skipped_mcp == 1
    *_, opted_in = sft.build(engine.store, engine.registry, users=users, include_mcp=True)
    assert opted_in.skipped_mcp == 0
    _, laya = tuning.build_rows(engine.store, engine.registry, users=users)
    assert laya.skipped_mcp == 1 and laya.rows == 0
    assert Settings.from_env({"TEMPO_DATA_DIR": "memory"}).train_on_mcp is False
    assert Settings.from_env({"TEMPO_DATA_DIR": "memory", "TEMPO_TRAIN_ON_MCP": "1"}).train_on_mcp
