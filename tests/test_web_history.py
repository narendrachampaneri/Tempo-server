"""The web app's back end: saved chats (per user, private, never training data), Private chats
that leave no trace, voice endpoints against a fake speech provider, and the bundled files."""

import json

import httpx
from conftest import make_engine, make_registry
from fastapi.testclient import TestClient

from tempo import history, sft, speech
from tempo.api import create_app
from tempo.config import Settings
from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.types import ModelInfo, ProviderInfo


def client_for(**kw):
    engine, backend = make_engine(**kw)
    return TestClient(create_app(engine=engine, settings=Settings())), engine, backend


CHAT = {
    "title": "Trip to Jaipur",
    "messages": [
        {"role": "user", "content": "Plan a trip to Jaipur"},
        {"role": "assistant", "content": "Day 1: Amber Fort.", "model": "beta/mid", "stages": 2},
    ],
}


def test_save_list_open_rename_pin_delete():
    client, _, _ = client_for()
    saved = client.put("/api/chats/abcdef123456", json=CHAT).json()
    assert saved["title"] == "Trip to Jaipur"
    listed = client.get("/api/chats").json()["chats"]
    assert [c["id"] for c in listed] == ["abcdef123456"] and not listed[0]["pinned"]
    opened = client.get("/api/chats/abcdef123456").json()
    assert opened["messages"] == CHAT["messages"]  # exactly as it was

    client.patch("/api/chats/abcdef123456", json={"title": "Jaipur plan", "pinned": True})
    # a later save from the browser keeps the user's own title and the pin
    client.put("/api/chats/abcdef123456", json={**CHAT, "title": "auto title"})
    again = client.get("/api/chats/abcdef123456").json()
    assert again["title"] == "Jaipur plan" and again["pinned"] is True

    assert client.delete("/api/chats/abcdef123456").status_code == 200
    assert client.get("/api/chats/abcdef123456").status_code == 404
    assert client.get("/api/chats").json()["chats"] == []


def test_search_finds_words_inside_messages_and_pinned_come_first():
    client, _, _ = client_for()
    client.put("/api/chats/chat00000001", json=CHAT)
    other = {"title": "Other", "messages": [{"role": "user", "content": "How do tides work?"}]}
    client.put("/api/chats/chat00000002", json=other)
    hits = client.get("/api/chats", params={"q": "amber"}).json()["chats"]
    assert [c["id"] for c in hits] == ["chat00000001"] and "amber" in hits[0]["snippet"]
    assert client.get("/api/chats", params={"q": "100%_"}).json()["chats"] == []  # no wildcards
    client.patch("/api/chats/chat00000002", json={"pinned": True})
    assert client.get("/api/chats").json()["chats"][0]["id"] == "chat00000002"


def test_chats_belong_to_their_user():
    engine, _ = make_engine()
    asha, key = engine.accounts.create_user("asha")
    ben, ben_key = engine.accounts.create_user("ben")
    client = TestClient(create_app(engine=engine, settings=Settings()))
    as_asha = {"Authorization": f"Bearer {key}"}
    as_ben = {"Authorization": f"Bearer {ben_key}"}
    assert client.put("/api/chats/asha0000001", json=CHAT, headers=as_asha).status_code == 200
    assert client.get("/api/chats", headers=as_ben).json()["chats"] == []
    assert client.get("/api/chats/asha0000001", headers=as_ben).status_code == 404
    assert client.delete("/api/chats/asha0000001", headers=as_ben).status_code == 404
    assert client.get("/api/chats/asha0000001/export", headers=as_ben).status_code == 404
    assert len(client.get("/api/chats", headers=as_asha).json()["chats"]) == 1
    assert engine.accounts.delete_data(asha.id) == 0  # deleting "my data" removes chats too
    assert client.get("/api/chats", headers=as_asha).json()["chats"] == []
    del ben


def test_export_markdown_and_json():
    client, _, _ = client_for()
    client.put("/api/chats/chat00000001", json=CHAT)
    md = client.get("/api/chats/chat00000001/export?format=md")
    assert md.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''Trip-to-Jaipur.md"
    )
    text = md.text
    assert (
        text.startswith("# Trip to Jaipur") and "## You" in text and "## Tempo · beta/mid" in text
    )
    assert "Day 1: Amber Fort." in text
    body = client.get("/api/chats/chat00000001/export?format=json").json()
    assert body["messages"][1]["content"] == "Day 1: Amber Fort."
    assert client.get("/api/chats/chat00000001/export?format=pdf").status_code == 400


def test_bad_chats_are_refused_with_a_reason():
    client, _, _ = client_for()
    assert client.put("/api/chats/x", json=CHAT).status_code == 400  # id too short
    assert client.put("/api/chats/chat00000001", json={"messages": "no"}).status_code == 400
    huge = {"messages": [{"role": "user", "content": "x" * (history.MAX_CHAT_BYTES + 1)}]}
    r = client.put("/api/chats/chat00000001", json=huge)
    assert r.status_code == 400 and "too large" in r.json()["error"]["message"]


def test_saved_chats_never_reach_training_exports():
    client, engine, _ = client_for()
    client.put("/api/chats/chat00000001", json=CHAT)
    for provider in engine.registry.providers.values():
        provider.training_on_outputs = "yes"
    rows, _, _ = sft.build(engine.store, engine.registry, users=sft.DEFAULT_USERS)
    assert "Jaipur" not in json.dumps(rows)
    # history is its own table: the question log, which exports read, has nothing from it
    assert not engine.store.query("SELECT 1 FROM questions WHERE messages LIKE '%Jaipur%'")


def sse(response):
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def test_private_questions_leave_nothing_behind():
    client, engine, _ = client_for()
    ask = {"messages": [{"role": "user", "content": "Secret plan"}], "mode": "auto"}
    kept = sse(client.post("/api/ask", json=ask))
    assert any(e["type"] == "done" for e in kept)
    assert engine.store.query("SELECT 1 FROM questions")  # normal chats are logged as before
    for table in ("questions", "calls", "stages"):
        engine.store.execute(f"DELETE FROM {table}")

    private = sse(client.post("/api/ask", json={**ask, "save": False}))
    assert any(e["type"] == "done" for e in private)
    for table in ("questions", "calls", "stages", "decisions"):
        assert not engine.store.query(f"SELECT * FROM {table}"), table


def test_web_files_are_served_with_cache_rules():
    client, _, _ = client_for()
    page = client.get("/")
    assert "/static/js/app.js" in page.text and "cdn" not in page.text.lower()
    js = client.get("/static/js/app.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"
    font = client.get("/static/fonts/inter-latin-wght-normal.woff2")
    assert font.status_code == 200 and "max-age" in font.headers["cache-control"]
    assert client.get("/static/../pipeline.py").status_code in (404, 400)


def test_no_external_urls_in_the_web_app():
    """Everything is bundled: no CDN, no trackers (checked over the app's own code)."""
    import re
    from pathlib import Path

    web = Path(__file__).parent.parent / "tempo" / "web"
    allowed = ("http://www.w3.org/", "https://tempo.dev")
    for path in [web / "index.html", *web.glob("css/*.css"), *web.glob("js/*.js")]:
        text = path.read_text(encoding="utf-8")
        for url in re.findall(
            r"""(?:src|href|url\(|import\s.*from\s|fetch\()\s*=?\s*["']?(https?://[^"')\s]+)""",
            text,
        ):
            assert url.startswith(allowed), f"{path.name} loads {url}"


# --- voice ---------------------------------------------------------------------------------


def build_speech_app(handler, with_key=True):
    from conftest import ScriptedBackend

    registry = make_registry({"GROQ_API_KEY": "gsk_placeholder"} if with_key else {})
    registry.providers["groq"] = ProviderInfo(id="groq", label="Groq", key_env="GROQ_API_KEY")
    registry.add(
        ModelInfo(id="groq/whisper-large-v3", provider="groq", name="W", type="speech-to-text")
    )
    registry.add(
        ModelInfo(
            id="groq/canopylabs/orpheus-v1-english",
            provider="groq",
            name="O",
            type="text-to-speech",
        )
    )
    engine = Engine(
        registry, lambda m: ScriptedBackend(), settings=Settings(), health=HealthTracker()
    )
    app = create_app(engine=engine, settings=Settings())
    app.state.speech_transport = httpx.MockTransport(handler)
    return TestClient(app)


def test_voice_is_hidden_without_a_key_and_shown_with_one():
    client = build_speech_app(lambda r: httpx.Response(500), with_key=False)
    assert client.get("/api/capabilities").json()["speech"]["transcribe"] is None
    assert client.get("/api/capabilities").json()["speech"]["say"] is None
    r = client.post("/api/speech/say", json={"text": "hello"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "unavailable"
    client = build_speech_app(lambda r: httpx.Response(500))
    speech_caps = client.get("/api/capabilities").json()["speech"]
    assert speech_caps["transcribe"] == "groq/whisper-large-v3"
    assert speech_caps["say"] == "groq/canopylabs/orpheus-v1-english"


def test_transcribe_and_say_call_the_provider_with_the_callers_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": " what is 2+2 "})
        return httpx.Response(200, content=b"RIFFfakewav")

    client = build_speech_app(handler)
    r = client.post(
        "/api/speech/transcribe",
        content=b"\x1aE\xdf\xa3" * 300,
        headers={"Content-Type": "audio/webm"},
    )
    assert r.json() == {"text": "what is 2+2"}
    sent = seen["/openai/v1/audio/transcriptions"]
    assert sent.headers["authorization"] == "Bearer gsk_placeholder"
    assert b"whisper-large-v3" in sent.content and b"speech.webm" in sent.content

    r = client.post("/api/speech/say", json={"text": "Hello there."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFFfakewav"
    body = json.loads(seen["/openai/v1/audio/speech"].content)
    assert body["model"] == "canopylabs/orpheus-v1-english" and body["input"] == "Hello there."
    too_long = client.post("/api/speech/say", json={"text": "word " * 60})
    assert too_long.status_code == 400 and too_long.json()["error"]["code"] == "bad_length"


def test_speech_errors_say_what_to_do_next():
    def limited(request):
        return httpx.Response(
            429, headers={"retry-after": "120"}, json={"error": {"message": "slow"}}
        )

    r = build_speech_app(limited).post("/api/speech/say", json={"text": "hi"})
    assert r.status_code == 429 and r.headers["retry-after"] == "120"
    assert "try again later" in r.json()["error"]["message"].lower()

    def terms(request):
        return httpx.Response(400, json={"error": {"message": "model terms required"}})

    r = build_speech_app(terms).post("/api/speech/say", json={"text": "hi"})
    assert r.json()["error"]["code"] == "terms_not_accepted"

    def rejected(request):
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    r = build_speech_app(rejected).post(
        "/api/speech/transcribe", content=b"x" * 2000, headers={"Content-Type": "audio/webm"}
    )
    assert r.json()["error"]["code"] == "key_rejected" and "gsk_" not in r.text


def test_split_for_speech_respects_the_model_limit():
    text = (
        "First sentence here. "
        + ("word " * 80)
        + "Last one. ```code``` **bold** [link](http://x.y)"
    )
    pieces = speech.split_for_speech(text)
    assert pieces and all(len(p) <= speech.TTS_MAX_CHARS for p in pieces)
    assert "code omitted" in " ".join(pieces) and "http" not in " ".join(pieces)


def test_docs_site_uses_the_apps_own_theme_and_fonts():
    """docs/ (GitHub Pages) can't reach tempo/web, so it carries copies of the theme and fonts.
    To refresh: cp tempo/web/css/*.css docs/assets/css/; cp tempo/web/fonts/* docs/assets/fonts/"""
    from pathlib import Path

    root = Path(__file__).parent.parent
    for folder in ("css", "fonts"):
        for source in sorted((root / "tempo" / "web" / folder).iterdir()):
            copy = root / "docs" / "assets" / folder / source.name
            assert copy.exists(), f"{copy} is missing (see this test's docstring)"
            assert copy.read_bytes() == source.read_bytes(), f"{copy} is out of date"
