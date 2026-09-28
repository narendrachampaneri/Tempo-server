import json

import pytest
from conftest import make_engine
from fastapi.testclient import TestClient
from openai import OpenAI

from tempo.api import create_app
from tempo.config import Settings
from tempo.providers import ProviderError

HELLO = [{"role": "user", "content": "hi"}]


def client_for(scripts=None, env=None, api_key=None):
    engine, backend = make_engine(scripts, env=env)
    app = create_app(engine=engine, settings=Settings(api_key=api_key))
    return TestClient(app), backend


def sse_payloads(body: str) -> list:
    out = []
    for line in body.splitlines():
        if line.startswith("data: "):
            data = line[len("data: ") :]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


def test_web_page_and_health():
    client, _ = client_for()
    page = client.get("/")
    assert page.status_code == 200 and "<title>Tempo</title>" in page.text
    assert client.get("/health").json()["models_ready"] == 4


def test_models_endpoint_lists_virtual_and_ready_models():
    client, _ = client_for(env={"BETA_KEY": "b"})
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids[:4] == ["tempo/auto", "tempo/fast", "tempo/best", "tempo/private"]
    assert ids[4:] == ["beta/mid"]


def test_chat_completion_non_streaming():
    client, _ = client_for()
    r = client.post("/v1/chat/completions", json={"model": "tempo/auto", "messages": HELLO})
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "beta/mid"
    message = body["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Answer from beta/mid."}
    assert body["tempo"]["routed_to"] == "beta/mid"
    assert "trace" not in body["tempo"]


def test_chat_completion_trace_and_conditions():
    client, backend = client_for()
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "tempo",
            "messages": HELLO,
            "tempo": {"privacy": "local_only", "trace": True},
        },
    )
    body = r.json()
    assert body["model"] == "local/tiny"
    assert [e["type"] for e in body["tempo"]["trace"]][:4] == [
        "received",
        "analyze",
        "plan",
        "stage_start",
    ]
    assert body["tempo"]["stages"] >= 1 and body["tempo"]["question_id"]
    # The OpenAI endpoint does not inject Tempo's system prompt.
    assert all(m["role"] != "system" for m in backend.calls[-1][1])


def test_explicit_registry_model_and_unknown_model():
    client, _ = client_for()
    r = client.post("/v1/chat/completions", json={"model": "alpha/small", "messages": HELLO})
    assert r.json()["model"] == "alpha/small"

    r = client.post("/v1/chat/completions", json={"model": "gpt-9", "messages": HELLO})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model_not_found"


def test_no_available_model_returns_503():
    client, _ = client_for(env={"ALPHA_KEY": "a"})
    r = client.post("/v1/chat/completions", json={"model": "tempo/private", "messages": HELLO})
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "tempo_error"


def stream_parts(chunks):
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    reasoning = [c["choices"][0]["delta"].get("reasoning_content") for c in chunks if c["choices"]]
    return content, reasoning


def test_streaming_sends_the_checked_final_answer_with_trace_chunks():
    client, _ = client_for(
        {"beta/mid": [("reasoning", "hmm"), ("answer", "Hel"), ("answer", "lo")]}
    )
    r = client.post(
        "/v1/chat/completions",
        json={"model": "tempo/auto", "messages": HELLO, "stream": True, "tempo": {"trace": True}},
    )
    payloads = sse_payloads(r.text)
    assert payloads[-1] == "[DONE]"
    chunks = payloads[:-1]
    content, _ = stream_parts(chunks)
    assert content == "Hello"
    trace = [c["tempo"]["event"]["type"] for c in chunks if "tempo" in c]
    assert {"stage_start", "check", "stage_end", "answer_final"} <= set(trace)
    # Content only arrives after the check, so the trace comes first.
    first_content = next(i for i, c in enumerate(chunks) if c["choices"])
    assert first_content > trace.index("check")
    finals = [c for c in chunks if c["choices"] and c["choices"][0]["finish_reason"] == "stop"]
    assert len(finals) == 1 and finals[0]["model"] == "beta/mid"


def test_one_stage_stream_is_live_and_carries_reasoning():
    client, _ = client_for(
        {"beta/mid": [("reasoning", "hmm"), ("answer", "Hel"), ("answer", "lo")]}
    )
    r = client.post(
        "/v1/chat/completions",
        json={"model": "tempo/auto", "messages": HELLO, "stream": True, "tempo": {"max_stages": 1}},
    )
    content, reasoning = stream_parts(sse_payloads(r.text)[:-1])
    assert content == "Hello" and "hmm" in reasoning


def test_live_stream_does_not_restart_after_partial_output():
    client, _ = client_for(
        {"beta/mid": [("answer", "Part"), ProviderError("unavailable", "reset")]}
    )
    r = client.post(
        "/v1/chat/completions",
        json={"model": "tempo/auto", "messages": HELLO, "stream": True, "tempo": {"max_stages": 1}},
    )
    payloads = sse_payloads(r.text)
    assert payloads[-1]["error"]["type"] == "tempo_error"
    assert "failed mid-answer" in payloads[-1]["error"]["message"]


def test_checked_stream_recovers_from_a_mid_answer_failure():
    client, _ = client_for(
        {"beta/mid": [("answer", "Part"), ProviderError("unavailable", "reset")]}
    )
    r = client.post(
        "/v1/chat/completions", json={"model": "tempo/auto", "messages": HELLO, "stream": True}
    )
    payloads = sse_payloads(r.text)
    assert payloads[-1] == "[DONE]"
    content, _ = stream_parts(payloads[:-1])
    assert "Part" not in content and content.startswith("Answer from")


def test_stage_options_are_validated():
    client, _ = client_for()
    bad = {"model": "tempo/auto", "messages": HELLO, "tempo": {"max_stages": 500}}
    assert client.post("/v1/chat/completions", json=bad).status_code == 422
    ok = {"model": "tempo/auto", "messages": HELLO, "tempo": {"max_stages": 25}}
    assert client.post("/v1/chat/completions", json=ok).status_code == 200


def test_ask_endpoint_streams_trace_events():
    client, backend = client_for({"beta/mid": [ProviderError("rate_limit", "429")]})
    r = client.post("/api/ask", json={"prompt": "hi", "mode": "auto"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_payloads(r.text)
    kinds = [e["type"] for e in events]
    assert kinds[:4] == ["received", "analyze", "plan", "stage_start"]
    assert "fallback" in kinds and kinds[-1] == "done"
    final = next(e for e in events if e["type"] == "answer_final")
    assert final["answer"].startswith("Answer from")
    assert final["text"].startswith("Final answer from")
    fallback = next(e for e in events if e["type"] == "fallback")
    assert fallback["text"].startswith("Falling back →")
    deltas = [e for e in events if e["type"] == "answer_delta"]
    assert deltas and all("delta" in e and "text" not in e for e in deltas)
    # The native endpoint adds Tempo's system prompt.
    assert backend.calls[-1][1][0]["role"] == "system"


def test_ask_requires_prompt_or_messages():
    client, _ = client_for()
    assert client.post("/api/ask", json={}).status_code == 400


def test_models_detail_endpoint():
    client, _ = client_for(env={"BETA_KEY": "b"})
    data = client.get("/api/models").json()
    providers = {p["id"]: p for p in data["providers"]}
    assert providers["beta"]["configured"] and not providers["alpha"]["configured"]
    statuses = {m["id"]: m["status"] for m in data["models"]}
    assert statuses == {
        "alpha/strong": "not configured",
        "alpha/small": "not configured",
        "beta/mid": "ready",
        "local/tiny": "not configured",
    }


@pytest.mark.parametrize("path", ["/v1/models", "/api/models"])
def test_api_key_is_enforced_when_configured(path):
    client, _ = client_for(api_key="s3cret")
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_official_openai_sdk_works_against_tempo():
    client, _ = client_for({"beta/mid": [("answer", "Hello"), ("answer", " there")]})
    sdk = OpenAI(base_url="http://testserver/v1", api_key="unused", http_client=client)

    reply = sdk.chat.completions.create(model="tempo/auto", messages=HELLO)
    assert reply.choices[0].message.content == "Hello there"
    assert reply.model == "beta/mid"

    stream = sdk.chat.completions.create(
        model="tempo/auto", messages=HELLO, stream=True, extra_body={"tempo": {"trace": True}}
    )
    text = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert text == "Hello there"

    assert "tempo/auto" in [m.id for m in sdk.models.list()]


def test_feedback_is_recorded():
    client, _ = client_for()
    events = sse_payloads(client.post("/api/ask", json={"prompt": "hi"}).text)
    question_id = next(e for e in events if e["type"] == "done")["question_id"]
    r = client.post("/api/feedback", json={"question_id": question_id, "rating": 1})
    assert r.status_code == 200
    engine = client.app.state.engine
    assert engine.store.question(question_id)["feedback"] == 1
    assert (
        client.post("/api/feedback", json={"question_id": "nope", "rating": -1}).status_code == 404
    )
    assert (
        client.post("/api/feedback", json={"question_id": question_id, "rating": 5}).status_code
        == 422
    )
