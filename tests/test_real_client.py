"""The official OpenAI Python SDK against a real Tempo-server over HTTP (demo models, no keys):
chat, streaming, tool calls (native and emulated), tool results, strict JSON and images."""

import json
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from openai import OpenAI

from tempo.api import create_app
from tempo.config import Settings
from tempo.engine import Engine

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
PERSON = {
    "type": "json_schema",
    "json_schema": {
        "name": "person",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name", "age"],
            "additionalProperties": False,
        },
    },
}
IMAGE = "data:image/png;base64,iVBORw0KGgo="


@contextmanager
def running(engine, settings):
    """A real uvicorn server on a free local port; yields its /v1 base URL."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_app(engine, settings), host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def demo_settings(**overrides):
    values = dict(
        enable_mock=True,
        data_dir=None,
        embeddings="off",
        laya="off",
        sync_interval_s=0,
        cache=False,
    )
    return Settings(**{**values, **overrides})


def client(base_url, api_key="unused"):
    http = httpx.Client(trust_env=False, timeout=60)  # straight to localhost, no proxy
    return OpenAI(base_url=base_url, api_key=api_key, http_client=http)


@pytest.fixture(scope="module")
def sdk():
    settings = demo_settings()
    with running(Engine.from_settings(settings), settings) as base_url:
        yield client(base_url)


def test_chat_and_streaming(sdk):
    reply = sdk.chat.completions.create(
        model="tempo/auto", messages=[{"role": "user", "content": "hi there"}]
    )
    assert reply.choices[0].message.content and reply.model.startswith("mock/")
    text = "".join(
        c.choices[0].delta.content or ""
        for c in sdk.chat.completions.create(
            model="tempo/auto", messages=[{"role": "user", "content": "hi there"}], stream=True
        )
        if c.choices
    )
    assert "offline demo answer" in text
    assert "tempo/auto" in [m.id for m in sdk.models.list()]


@pytest.mark.parametrize("model", ["mock/smart", "mock/fast"])  # native, then emulated tools
def test_tool_calls_and_tool_results(sdk, model):
    ask = [{"role": "user", "content": "What's the weather in Paris?"}]
    reply = sdk.chat.completions.create(model=model, messages=ask, tools=[WEATHER])
    choice = reply.choices[0]
    assert choice.finish_reason == "tool_calls"
    call = choice.message.tool_calls[0]
    assert call.function.name == "get_weather" and json.loads(call.function.arguments)["city"]

    follow_up = [
        *ask,
        choice.message.model_dump(exclude_none=True),
        {"role": "tool", "tool_call_id": call.id, "content": '{"temp_c": 21}'},
    ]
    answer = sdk.chat.completions.create(model=model, messages=follow_up, tools=[WEATHER])
    assert answer.choices[0].finish_reason == "stop"
    assert "temp_c" in answer.choices[0].message.content

    name, args = "", ""
    for chunk in sdk.chat.completions.create(
        model=model, messages=ask, tools=[WEATHER], stream=True
    ):
        for piece in (chunk.choices[0].delta.tool_calls or []) if chunk.choices else []:
            name += piece.function.name or ""
            args += piece.function.arguments or ""
    assert name == "get_weather" and "city" in json.loads(args)


def test_strict_json_schema(sdk):
    ask = [{"role": "user", "content": "Asha is 34. Return the person."}]
    reply = sdk.chat.completions.create(model="tempo/auto", messages=ask, response_format=PERSON)
    value = json.loads(reply.choices[0].message.content)
    assert set(value) == {"name", "age"} and isinstance(value["age"], int)
    streamed = "".join(
        c.choices[0].delta.content or ""
        for c in sdk.chat.completions.create(
            model="tempo/auto", messages=ask, response_format=PERSON, stream=True
        )
        if c.choices
    )
    assert json.loads(streamed) == value


def test_image_input_goes_to_a_vision_model(sdk):
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this picture?"},
            {"type": "image_url", "image_url": {"url": IMAGE}},
        ],
    }
    reply = sdk.chat.completions.create(model="tempo/auto", messages=[message])
    assert reply.model == "mock/smart"  # the only working vision model in demo mode
    assert "vision model" in reply.choices[0].message.content
