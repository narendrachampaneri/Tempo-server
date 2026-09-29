"""The Python SDK against a real Tempo-server in demo mode (offline demo models, no provider
keys). Uses $TEMPO_URL and $TEMPO_API_KEY when set (CI starts the server); otherwise starts one
with `python -m tempo.cli serve` and a user made with `tempo-server users add`."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tempo_server_client import AsyncTempoClient, TempoClient, TempoError

QUESTION = "What is 17% of 2,340?"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if os.environ.get("TEMPO_URL") and os.environ.get("TEMPO_API_KEY"):
        yield os.environ["TEMPO_URL"], os.environ["TEMPO_API_KEY"]
        return
    data = tmp_path_factory.mktemp("tempo-data")
    env = {
        **os.environ,
        "TEMPO_DATA_DIR": str(data),
        "TEMPO_ENABLE_MOCK": "1",
        "TEMPO_EMBEDDINGS": "off",
        "TEMPO_LAYA": "off",
        "TEMPO_SYNC_INTERVAL": "0",
        "TEMPO_CACHE": "off",
        "PYTHONUTF8": "1",
    }
    env.pop("TEMPO_API_KEY", None)
    made = subprocess.run(
        [sys.executable, "-m", "tempo.cli", "users", "add", "sdk-test"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    key = made.stdout.strip().splitlines()[-1]
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tempo.cli", "serve", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(url + "/health", timeout=1, trust_env=False).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("demo server did not start")
    yield url, key
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def tempo(server):
    url, key = server
    with TempoClient(url, key, http_client=httpx.Client(trust_env=False, timeout=120)) as client:
        yield client


def test_stream_shows_the_thinking_window_live(tempo):
    events = list(tempo.stream(QUESTION, max_stages=3))
    types = [e.type for e in events]
    assert types[0] == "received" and types[-1] == "done"
    assert {"analyze", "plan", "stage_start", "answer_delta"} <= set(types)
    assert all(e.text for e in events if e.type in ("stage_start", "done"))
    assert events[-1]["stop_reason"]


def test_ask_folds_the_answer(tempo):
    answer = tempo.ask(QUESTION, mode="fast")
    assert answer.text and answer.model and answer.model.startswith("mock/")
    assert answer.question_id and answer.stages >= 1 and answer.trace
    assert answer.error is None
    chat = tempo.ask(messages=[{"role": "user", "content": "hi"}], privacy="no_logging")
    assert chat.text


def test_feedback_consent_and_delete(tempo):
    answer = tempo.ask("hi there")
    tempo.feedback(answer.question_id, "up", comment="helpful")
    tempo.feedback(answer.question_id, -1)
    with pytest.raises(ValueError):
        tempo.feedback(answer.question_id, 5)
    assert tempo.consent() is False  # off by default for users
    assert tempo.set_consent(True) is True and tempo.consent() is True
    assert tempo.set_consent(False) is False
    assert tempo.delete_my_data() >= 1
    with pytest.raises(TempoError) as err:
        tempo.feedback(answer.question_id, 1)  # the question is gone
    assert err.value.status == 404


def test_models_quota_me_health(tempo):
    models = tempo.models()
    assert models["models"] and {"id", "status", "provider"} <= set(models["models"][0])
    quota = tempo.quota()
    assert "providers" in quota and quota["all_used_up"] is False
    assert tempo.me()["user"] == "sdk-test"
    assert tempo.health()["status"] == "ok"


def test_a_wrong_key_raises(server):
    url, _ = server
    client = TempoClient(url, "not-a-tempo-key", http_client=httpx.Client(trust_env=False))
    with pytest.raises(TempoError) as err:
        client.quota()
    assert err.value.status == 401 and "API key" in err.value.message
    with pytest.raises(TempoError):
        list(client.stream("hi"))


def test_async_client(server):
    url, key = server

    async def main():
        http = httpx.AsyncClient(trust_env=False, timeout=120)
        async with AsyncTempoClient(url, key, http_client=http) as tempo:
            seen = [e.type async for e in tempo.stream("hi")]
            answer = await tempo.ask(QUESTION)
            quota = await tempo.quota()
            return seen, answer, quota

    seen, answer, quota = asyncio.run(main())
    assert seen[0] == "received" and seen[-1] == "done"
    assert answer.text and "providers" in quota
