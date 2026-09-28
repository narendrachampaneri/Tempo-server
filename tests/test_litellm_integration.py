"""Real LiteLLM calls against a local fake Groq (OpenAI-style) and Ollama server.

Guards the parts unit tests can't: LiteLLM's streaming parsers, how its exceptions map to
Tempo's error kinds, and credentials/base URLs reaching the provider.
"""

import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tempo.config import Settings
from tempo.engine import Engine
from tempo.providers import LiteLLMBackend, ProviderError
from tempo.registry import Registry

fake = FastAPI()
seen: list[dict] = []


@fake.post("/openai/v1/chat/completions")
async def _chat(req: Request):
    body = await req.json()
    model = body["model"]
    seen.append({"model": model, "auth": req.headers.get("authorization")})
    if model == "openai/gpt-oss-120b":
        return JSONResponse(
            {"error": {"message": "Rate limit reached"}},
            status_code=429,
            headers={"retry-after": "7"},
        )
    if model == "llama-3.1-8b-instant":
        return JSONResponse({"error": {"message": "Invalid API Key"}}, status_code=401)
    pieces = ["<thi", "nk>Plan.</th", "ink>\n\n", "Hello ", "from ", model]

    async def gen():
        for i, piece in enumerate(pieces):
            delta = {"content": piece} | ({"role": "assistant"} if i == 0 else {})
            chunk = {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@fake.get("/api/tags")
async def _tags():
    return {"models": [{"name": "qwen2.5:7b", "details": {"parameter_size": "7.6B"}}]}


@fake.post("/api/chat")
async def _ollama_chat(req: Request):
    body = await req.json()

    async def gen():
        for word in ["Local ", "answer."]:
            yield (
                json.dumps(
                    {
                        "model": body["model"],
                        "message": {"role": "assistant", "content": word},
                        "done": False,
                    }
                )
                + "\n"
            )
        yield (
            json.dumps(
                {
                    "model": body["model"],
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                }
            )
            + "\n"
        )

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@pytest.fixture(scope="module")
def base_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(fake, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "fake provider server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(base_url, monkeypatch):
    values = {
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "GROQ_API_KEY": "test-key",
        "GROQ_API_BASE": f"{base_url}/openai/v1",
        "OLLAMA_API_BASE": base_url,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for key in ("CEREBRAS_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "TEMPO_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return values


async def _collect(backend, model_id, registry):
    out = []
    async for delta in backend.stream(registry.get(model_id), [{"role": "user", "content": "hi"}]):
        out.append(delta)
    return out


async def test_streams_answer_and_splits_think_tags(env):
    registry = Registry.load(env=env)
    deltas = await _collect(LiteLLMBackend(registry, timeout=10), "groq/qwen/qwen3-32b", registry)
    answer = "".join(t for k, t in deltas if k == "answer")
    reasoning = "".join(t for k, t in deltas if k == "reasoning")
    assert answer == "Hello from qwen/qwen3-32b"
    assert reasoning == "Plan."
    assert seen[-1] == {"model": "qwen/qwen3-32b", "auth": "Bearer test-key"}


@pytest.mark.parametrize(
    ("model_id", "kind", "retry_after"),
    [("groq/openai/gpt-oss-120b", "rate_limit", 7.0), ("groq/llama-3.1-8b-instant", "auth", None)],
)
async def test_provider_errors_are_classified(env, model_id, kind, retry_after):
    registry = Registry.load(env=env)
    with pytest.raises(ProviderError) as info:
        await _collect(LiteLLMBackend(registry, timeout=10), model_id, registry)
    assert info.value.kind == kind
    assert info.value.retry_after == retry_after


async def test_ollama_streaming(env):
    registry = Registry.load(env=env)
    await registry.discover_ollama()
    backend = LiteLLMBackend(registry, timeout=10)
    deltas = await _collect(backend, "ollama_chat/qwen2.5:7b", registry)
    assert "".join(t for _, t in deltas) == "Local answer."


async def test_engine_end_to_end_falls_back_after_rate_limit(env):
    engine = Engine.from_settings(Settings.from_env(env))
    await engine.startup()
    result = await engine.complete(
        [{"role": "user", "content": "hi"}], engine.options(model="groq/openai/gpt-oss-120b")
    )
    assert result.error is None
    kinds = [e.data.get("kind") for e in result.events if e.type == "call_error"]
    assert kinds == ["rate_limit"]
    assert result.model != "groq/openai/gpt-oss-120b"
    assert result.text
