"""docs/CONNECT.md: what every app does, with the OpenAI Python SDK against a real server. The
base URL is http://localhost:8000/v1, the model tempo/auto, and the key is the user's own
Tempo key (from `tempo users add`); any other key is refused once users exist."""

import openai
import pytest
from test_real_client import client, demo_settings, running

from tempo.engine import Engine


@pytest.fixture(scope="module")
def server():
    settings = demo_settings()
    engine = Engine.from_settings(settings)
    _, tempo_key = engine.accounts.create_user("asha")
    with running(engine, settings) as base_url:
        yield base_url, tempo_key


def test_an_app_connects_with_the_users_tempo_key(server):
    base_url, tempo_key = server
    app = client(base_url, tempo_key)
    assert "tempo/auto" in [m.id for m in app.models.list()]
    reply = app.chat.completions.create(
        model="tempo/auto", messages=[{"role": "user", "content": "Say hello"}]
    )
    assert reply.choices[0].message.content
    streamed = "".join(
        c.choices[0].delta.content or ""
        for c in app.chat.completions.create(
            model="tempo/fast", messages=[{"role": "user", "content": "hi"}], stream=True
        )
        if c.choices
    )
    assert streamed


def test_a_wrong_key_is_refused(server):
    base_url, _ = server
    with pytest.raises(openai.AuthenticationError):
        client(base_url, "not-a-tempo-key").models.list()
