"""OpenAI API compatibility: tool calls on every provider, strict JSON schema, image input,
and streaming for all of them (step 4)."""

import json

import pytest
from conftest import make_engine
from fastapi.testclient import TestClient
from openai import OpenAI

from tempo import compat
from tempo.api import create_app
from tempo.config import Settings

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "unit": {"type": "string", "enum": ["c", "f"]},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}
TIME = {
    "type": "function",
    "function": {
        "name": "get_time",
        "parameters": {"type": "object", "properties": {"zone": {"type": "string"}}},
    },
}
ASK = [{"role": "user", "content": "What's the weather in Paris right now?"}]
PARIS = {"city": "Paris", "unit": "c"}


def call_text(fmt: str, args=PARIS, name="get_weather") -> str:
    body = json.dumps({"name": name, "arguments": args})
    return {
        "hermes": f"<tool_call>{body}</tool_call>",
        "mistral": f"[TOOL_CALLS] [{body}]",
        "llama": f'<|python_tag|>{{"name": "{name}", "parameters": {json.dumps(args)}}}',
        "function_tag": f"<function={name}>{json.dumps(args)}</function>",
        "fenced": f"Sure.\n```json\n{body}\n```",
        "bare": body,
    }[fmt]


def app_for(scripts=None, native=(), vision=()):
    engine, backend = make_engine(scripts)
    for model in engine.registry.all():
        model.tools = True if model.id in native else None
        model.vision = model.id in vision
    return TestClient(create_app(engine=engine, settings=Settings())), engine, backend


def post(client, **body):
    return client.post("/v1/chat/completions", json={"model": "tempo/auto", **body})


# --- parsing and validation ------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["hermes", "mistral", "llama", "function_tag", "fenced", "bare"])
def test_every_text_format_becomes_the_same_openai_tool_call(fmt):
    calls, rest = compat.parse_text_tool_calls(call_text(fmt), {"get_weather"})
    assert len(calls) == 1
    call = calls[0]
    assert call["type"] == "function" and call["id"].startswith("call_")
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == PARIS
    assert "get_weather" not in rest


def test_plain_json_is_not_a_tool_call_unless_it_names_a_tool():
    assert compat.parse_text_tool_calls('{"name": "Asha", "age": 3}', {"get_weather"})[0] == []


def test_tool_call_validation():
    req = compat.ToolRequest([WEATHER, TIME])
    ok = compat.make_call("get_weather", PARIS)
    assert compat.validate_tool_calls([ok], req) == []
    bad = [
        compat.make_call("get_stock", {}),
        compat.make_call("get_weather", "{not json"),
        compat.make_call("get_weather", {"unit": "kelvin"}),
    ]
    issues = compat.validate_tool_calls(bad, req)
    assert any("unknown function 'get_stock'" in i for i in issues)
    assert any("not valid JSON" in i for i in issues)
    assert any("'city' is a required property" in i for i in issues)
    assert any("'kelvin' is not one of" in i for i in issues)
    required = compat.ToolRequest([WEATHER], "required")
    assert "a tool call was required" in compat.validate_tool_calls([], required)[0]
    named = compat.ToolRequest(
        [WEATHER, TIME], {"type": "function", "function": {"name": "get_time"}}
    )
    assert any("'get_time' was required" in i for i in compat.validate_tool_calls([ok], named))
    single = compat.ToolRequest([WEATHER], parallel=False)
    assert compat.validate_tool_calls([ok, ok], single)


def test_emulated_conversation_turns_tool_turns_into_text():
    history = [
        *ASK,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [compat.make_call("get_weather", PARIS, "c1")],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 21}'},
    ]
    out = compat.emulated_messages(history, compat.ToolRequest([WEATHER]))
    assert out[0]["role"] == "system" and out[0]["content"].startswith(compat.EMULATION_MARK)
    assert "get_weather" in out[0]["content"] and "<tool_call>" in out[2]["content"]
    assert out[3] == {"role": "user", "content": 'Result of c1:\n{"temp": 21}'}


def test_json_answers_are_extracted_and_validated():
    fmt = compat.JsonFormat(
        {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    )
    assert compat.validate_json_answer('Here: ```json\n{"n": 3}\n```', fmt) == ([], '{"n": 3}')
    issues, _ = compat.validate_json_answer('{"n": "three"}', fmt)
    assert "'three' is not of type 'integer'" in issues[0]
    assert compat.validate_json_answer("no json here", fmt)[0] == ["the answer is not valid JSON"]
    assert compat.validate_json_answer("[1, 2]", compat.JsonFormat(None))[0]
    assert compat.example_for(WEATHER["function"]["parameters"]) == {"city": "example", "unit": "c"}


# --- tool calls through the API --------------------------------------------------------------


def test_native_tool_calls_are_passed_through_and_returned_in_openai_shape():
    native = [("tool_calls", json.dumps([compat.make_call("get_weather", PARIS, "call_1")]))]
    client, _, backend = app_for({"beta/mid": native}, native={"beta/mid"})
    r = post(client, messages=ASK, tools=[WEATHER]).json()
    choice = r["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == PARIS
    assert backend.extras[0]["tools"] == [WEATHER]  # native: tools sent to the provider
    assert backend.called_for("judge") == []  # a validated call needs no judge


@pytest.mark.parametrize("fmt", ["hermes", "mistral", "llama", "function_tag"])
def test_providers_without_native_tools_give_the_same_result(fmt):
    client, _, backend = app_for({"beta/mid": [("answer", call_text(fmt))]})
    r = post(client, messages=ASK, tools=[WEATHER]).json()
    call = r["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == PARIS
    assert r["choices"][0]["finish_reason"] == "tool_calls"
    sent = backend.calls[0][1]
    assert backend.extras[0] == {}  # emulated: no tools parameter
    assert sent[0]["role"] == "system" and compat.EMULATION_MARK in sent[0]["content"]
    assert sum(m["role"] == "system" for m in sent) == 1  # merged into one system message


def test_a_bad_tool_call_is_retried_on_another_model():
    wrong = [("answer", call_text("hermes", {"unit": "kelvin"}))]
    right = [("answer", call_text("hermes"))]
    client, _, backend = app_for(
        {"beta/mid": wrong, "alpha/small": right, "alpha/strong": right, "local/tiny": right}
    )
    r = post(client, messages=ASK, tools=[WEATHER], tempo={"trace": True}).json()
    assert r["model"] != "beta/mid" and r["choices"][0]["message"]["tool_calls"]
    errors = [e for e in r["tempo"]["trace"] if e["type"] == "call_error"]
    assert errors and errors[0]["kind"] == "invalid" and "city" in errors[0]["message"]


def test_required_tool_call_is_enforced_and_text_answers_pass_on_auto():
    text = [("answer", "It is sunny in Paris today, about 21 degrees.")]
    client, _, _ = app_for({"*:draft": text})
    auto = post(client, messages=ASK, tools=[WEATHER]).json()
    assert auto["choices"][0]["message"]["content"].startswith("It is sunny")
    assert auto["choices"][0]["finish_reason"] == "stop"
    required = post(client, messages=ASK, tools=[WEATHER], tool_choice="required")
    assert required.status_code == 503
    assert "No model gave a valid tool call" in required.text
    assert "a tool call was required" in required.text


def test_tool_results_are_sent_back_to_the_model():
    answer = [("answer", "It's 21°C and sunny in Paris.")]
    client, _, backend = app_for({"beta/mid": answer})
    history = [
        *ASK,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [compat.make_call("get_weather", PARIS, "c1")],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 21, "sky": "sunny"}'},
    ]
    r = post(client, messages=history, tools=[WEATHER]).json()
    assert r["choices"][0]["message"]["content"].startswith("It's 21")
    sent = json.dumps(backend.calls[0][1])
    assert "Result of c1" in sent and '\\"temp\\": 21' in sent


def test_bad_tool_requests_are_rejected_like_openai():
    client, _, _ = app_for()
    named = {"type": "function", "function": {"name": "nope"}}
    assert post(client, messages=ASK, tools=[WEATHER], tool_choice=named).status_code == 400
    assert post(client, messages=ASK, tool_choice="required").status_code == 400
    assert post(client, messages=ASK, response_format={"type": "json_schema"}).status_code == 400


# --- strict JSON schema ------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
    "additionalProperties": False,
}
FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "person", "schema": SCHEMA, "strict": True},
}
PERSON = [{"role": "user", "content": "Extract the person: Asha is 34 years old."}]


def test_json_schema_answer_is_validated_and_retried_on_another_model():
    bad = [("answer", '{"name": "Asha", "age": "thirty-four"}')]
    good = [("answer", 'Here you go:\n```json\n{"name": "Asha", "age": 34}\n```')]
    scripts = {
        "beta/mid:draft": bad,
        "*:draft": good,
        "*:fix": good,
        "*:judge": [("answer", '{"grades": [{"id": 1, "score": 9, "issues": []}]}')],
    }
    client, _, backend = app_for(scripts)
    r = post(client, messages=PERSON, response_format=FORMAT, tempo={"trace": True}).json()
    content = r["choices"][0]["message"]["content"]
    assert json.loads(content) == {"name": "Asha", "age": 34}
    assert content == '{"name": "Asha", "age": 34}'  # clean JSON, no prose or fences
    errors = [e for e in r["tempo"]["trace"] if e["type"] == "call_error"]
    assert errors and errors[0]["model"] == "beta/mid" and errors[0]["kind"] == "invalid"
    system = backend.calls[0][1][0]
    assert system["role"] == "system" and compat.JSON_MARK in system["content"]


def test_json_that_never_matches_is_an_error_not_a_bad_answer():
    client, _, _ = app_for({"*:draft": [("answer", "I can't do JSON today.")]})
    r = post(client, messages=PERSON, response_format=FORMAT)
    assert r.status_code == 503 and "No model gave a valid JSON answer" in r.text


def test_json_object_mode():
    client, _, _ = app_for(
        {
            "*:draft": [("answer", '{"ok": true}')],
            "*:judge": [("answer", '{"grades": [{"id": 1, "score": 9, "issues": []}]}')],
        }
    )
    r = post(client, messages=PERSON, response_format={"type": "json_object"}).json()
    assert json.loads(r["choices"][0]["message"]["content"]) == {"ok": True}


# --- image input -------------------------------------------------------------------------------

IMAGE = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this picture?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ],
    }
]


def test_images_go_only_to_vision_models():
    client, engine, backend = app_for(vision={"beta/mid"})
    r = post(client, messages=IMAGE, tempo={"trace": True}).json()
    assert r["model"] == "beta/mid"
    assert set(backend.called) == {"beta/mid"}  # drafts, fixes and judges alike
    first = backend.calls[0][1][-1]["content"]
    assert any(part.get("type") == "image_url" for part in first)  # the image reaches it
    no_vision = post(client, messages=IMAGE, model="alpha/strong")
    assert no_vision.status_code == 503 and "no image support" in no_vision.text


def test_images_with_no_vision_model_are_refused():
    client, _, _ = app_for()
    r = post(client, messages=IMAGE)
    assert r.status_code == 503 and "no image support" in r.text


# --- streaming, with the official OpenAI SDK ------------------------------------------------


def test_streaming_tool_calls_json_and_images_with_the_openai_sdk():
    scripts = {
        "beta/mid": [("answer", call_text("mistral"))],
        "*:judge": [("answer", '{"grades": [{"id": 1, "score": 9, "issues": []}]}')],
    }
    client, _, _ = app_for(scripts, vision={"beta/mid"})
    sdk = OpenAI(base_url="http://testserver/v1", api_key="unused", http_client=client)

    stream = sdk.chat.completions.create(
        model="tempo/auto", messages=ASK, tools=[WEATHER], stream=True
    )
    name, args, finish = "", "", None
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        for call in delta.tool_calls or []:
            name += call.function.name or ""
            args += call.function.arguments or ""
        finish = chunk.choices[0].finish_reason or finish
    assert name == "get_weather" and json.loads(args) == PARIS and finish == "tool_calls"

    reply = sdk.chat.completions.create(model="tempo/auto", messages=ASK, tools=[WEATHER])
    assert reply.choices[0].message.tool_calls[0].function.name == "get_weather"

    client2, _, _ = app_for(
        {"*:draft": [("answer", '{"name": "Asha", "age": 34}')], "*:judge": scripts["*:judge"]}
    )
    sdk2 = OpenAI(base_url="http://testserver/v1", api_key="unused", http_client=client2)
    text = "".join(
        c.choices[0].delta.content or ""
        for c in sdk2.chat.completions.create(
            model="tempo/auto", messages=PERSON, response_format=FORMAT, stream=True
        )
        if c.choices
    )
    assert json.loads(text) == {"name": "Asha", "age": 34}

    client3, _, _ = app_for({"beta/mid": [("answer", "A cat on a sofa.")]}, vision={"beta/mid"})
    sdk3 = OpenAI(base_url="http://testserver/v1", api_key="unused", http_client=client3)
    text = "".join(
        c.choices[0].delta.content or ""
        for c in sdk3.chat.completions.create(model="tempo/auto", messages=IMAGE, stream=True)
        if c.choices
    )
    assert text == "A cat on a sofa."
