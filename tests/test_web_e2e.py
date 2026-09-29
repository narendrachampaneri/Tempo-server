"""Browser tests for the web app (Playwright + Chromium) against a real server with scripted
models: the main flows, accessibility basics, and the screenshots in docs/screenshots.

Skipped when Playwright or its Chromium is missing (CI installs both and sets
TEMPO_REQUIRE_E2E=1 so they can never be skipped there). Screenshots are written to
docs/screenshots when TEMPO_SCREENSHOTS=1, else to a temporary folder.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from conftest import ENV_ALL, ScriptedBackend, judge_reply, make_engine, make_registry, sleep

from tempo.api import create_app
from tempo.config import Settings
from tempo.engine import Engine
from tempo.health import HealthTracker
from tempo.providers import ProviderError
from tempo.types import ModelInfo, ProviderInfo

REQUIRED = os.environ.get("TEMPO_REQUIRE_E2E") == "1"
try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    if REQUIRED:
        raise
    pytest.skip("Playwright is not installed (pip install playwright)", allow_module_level=True)

ROOT = Path(__file__).parent.parent
SHOTS = ROOT / "docs" / "screenshots"


def _png(width=8, height=8):
    import struct
    import zlib

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\xb0\x3a\x2e" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PNG_1PX = _png()
WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"

ANSWERS = {
    "table": (
        "Here is a comparison of the two protocols:\n\n"
        "| Protocol | Reliable | Speed |\n|---|:-:|--:|\n| TCP | yes | slower |\n| UDP | no | faster |\n\n"
        "Use **TCP** for web pages and _UDP_ for live calls.\n\n"
        "- First point\n  - A nested point\n- Second point\n\n> Tip: measure before you choose."
    ),
    "math": (
        "The area of a circle is $A = \\pi r^2$, and the sum of the first $n$ numbers is:\n\n"
        "$$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$$\n\nA price of $5 or $10 stays as text."
    ),
    "palindrome": (
        "Here is the function:\n\n```python\ndef is_palindrome(s: str) -> bool:\n"
        "    cleaned = ''.join(c.lower() for c in s if c.isalnum())\n    return cleaned == cleaned[::-1]  # done\n```\n\n"
        "Run it with `pytest`."
    ),
    "html": (
        'A tiny page:\n\n```html\n<!doctype html><html><body><h1 id="hi">Hello preview</h1>'
        "<script>document.getElementById('hi').textContent='Hello from script';"
        "fetch('https://example.com/leak').catch(function(){document.title='blocked'})</script></body></html>\n```"
    ),
    "svg": (
        'A red circle:\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
        '<circle id="c" cx="50" cy="50" r="40" fill="#b03a2e"/></svg>\n```'
    ),
    "mermaid": "The flow:\n\n```mermaid\ngraph TD\n  A[Question] --> B[Tempo]\n  B --> C[Answer]\n```",
}


def last_user_text(messages) -> str:
    content = [m for m in messages if m["role"] == "user"][-1]["content"]
    return content if isinstance(content, str) else " ".join(p.get("text", "") for p in content)


REVISE_DRAFT = "Draft that needs fixing:\n\n```python\ndef tidy(items):\n    return items\n```"


def judge_script(messages):
    """Grades 3 for the draft of the "revise" question (so it gets fixed), else 9; slowly, so
    the answer is visibly ready while the check runs."""
    grades = judge_reply(lambda cands: [3 if "needs fixing" in c else 9 for c in cands])(messages)
    return [sleep(0.8), *grades]


def draft_script(messages):
    text = last_user_text(messages).lower()
    if "boom" in text:
        raise ProviderError("unavailable", "boom")
    if "revise" in text:
        return [("answer", REVISE_DRAFT)]
    body = f"Sure. You asked: {text[:60]}"
    for key, answer in ANSWERS.items():
        if key in text:
            body = answer
    if "[attached file:" in text:
        body = f"I read the attachment. It says: {text.split(']' + chr(10), 1)[1][:8]}"
    if "slow" in text:
        return [("answer", "Working on it ")] + [
            x for i in range(40) for x in (("sleep", 0.25), ("answer", f"word{i} "))
        ]
    pieces = [body[i : i + 24] for i in range(0, len(body), 24)]
    out = []
    for piece in pieces:
        out += [("sleep", 0.005), ("answer", piece)]
    return out


@contextmanager
def running(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class Site:
    def __init__(self, url, engine, backend):
        self.url, self.engine, self.backend = url, engine, backend

    def reset(self):
        self.engine.store.execute("DELETE FROM chats")
        self.engine.store.execute("DELETE FROM questions")
        self.engine.health._model_until.clear()  # models a test made fail are usable again
        self.engine.health._provider_until.clear()


@pytest.fixture(scope="module")
def site():
    scripts = {"*:draft": draft_script, "*:judge": judge_script}
    engine, backend = make_engine(scripts=scripts, sync_interval_s=0)
    app = create_app(
        engine=engine, settings=Settings(data_dir=ROOT / "data")
    )  # (the store is in memory)
    with running(app) as url:
        yield Site(url, engine, backend)


@pytest.fixture(scope="module")
def voice_site():
    registry = make_registry({**ENV_ALL, "GROQ_API_KEY": "gsk_placeholder"})
    registry.providers["groq"] = ProviderInfo(id="groq", label="Groq", key_env="GROQ_API_KEY")
    for model_id, kind in (
        ("groq/whisper-large-v3", "speech-to-text"),
        ("groq/orpheus", "text-to-speech"),
    ):
        registry.add(ModelInfo(id=model_id, provider="groq", name=model_id, type=kind))
    backend = ScriptedBackend({"beta/mid": draft_script})
    engine = Engine(
        registry, lambda m: backend, settings=Settings(sync_interval_s=0), health=HealthTracker()
    )
    app = create_app(engine=engine, settings=Settings())
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, json={"text": "what is a table"})
        return httpx.Response(200, content=WAV)

    app.state.speech_transport = httpx.MockTransport(provider)
    with running(app) as url:
        site = Site(url, engine, backend)
        site.calls = calls
        yield site


# Module scope: Playwright's sync API keeps an event loop running in this thread until it stops,
# which would break later tests that call asyncio.run().
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(
                args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
            )
        except PlaywrightError as exc:  # pragma: no cover
            if REQUIRED:
                raise
            pytest.skip(f"Chromium is not available: {str(exc)[:80]}")
        yield b
        b.close()


def open_page(browser, site, width=1280, height=800, scheme="light", motion="no-preference", **kw):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion=motion,
        permissions=["clipboard-read", "clipboard-write", "microphone"],
        accept_downloads=True,
        **kw,
    )
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(f"pageerror: {e}"))
    page.on(
        "console", lambda m: page.errors.append(f"console: {m.text}") if m.type == "error" else None
    )
    page.goto(site.url + "/")
    expect(page.locator("#status-text")).to_contain_text("model")
    return page


@pytest.fixture
def page(browser, site):
    site.reset()
    p = open_page(browser, site)
    yield p
    assert not [
        e for e in p.errors if "example.com" not in e and "Content Security Policy" not in e
    ], p.errors
    p.context.close()


def ask(page, text, wait=True):
    page.fill("#input", text)
    page.press("#input", "Enter")
    if wait:
        finish(page)


def finish(page, timeout=30000):
    expect(page.locator("#send")).not_to_have_class(re.compile("stop"), timeout=timeout)
    expect(page.locator(".msg-bot").last.locator(".bot-tools")).to_be_visible(timeout=timeout)


# ---------------------------------------------------------------------------------------------


def test_first_load_is_fast_offline_and_needs_nothing_external(browser, site):
    site.reset()
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    hosts = set()
    page.on("request", lambda r: hosts.add(re.sub(r"^(\w+://[^/]+).*", r"\1", r.url)))
    start = time.time()
    page.goto(site.url + "/", wait_until="load")
    expect(page.locator("#status-text")).to_contain_text("model")
    loaded = time.time() - start
    assert hosts == {site.url}, hosts  # no CDN, no trackers
    timing = page.evaluate(
        "() => { const n = performance.getEntriesByType('navigation')[0]; return {dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd}; }"
    )
    fonts = page.evaluate("() => document.fonts.check('16px \"Inter Variable\"')")
    assert timing["load"] < 1000, timing  # first load under one second
    assert loaded < 2.5 and fonts
    weight = page.evaluate(
        "() => performance.getEntriesByType('resource').reduce((n, r) => n + r.transferSize, 0)"
    )
    assert weight < 500_000, weight  # the page itself is light; libraries load only when needed
    context.close()


def test_question_streams_with_timeline_and_saves_to_history(page):
    ask(page, "Explain TCP", wait=False)
    timeline = page.locator(".thinking .tl")
    expect(timeline.locator(".node").first).to_be_visible()
    expect(page.locator(".mchip").first).to_be_visible()  # models appear as they are called
    finish(page)
    expect(page.locator(".msg-bot .thinking-head .title")).to_have_text("Thought it through")
    page.click(".thinking-head")
    expect(page.locator(".node.done").first).to_be_visible()
    assert page.locator(".node").count() >= 3
    assert "Drafting an answer" in page.locator(".tl").inner_text()
    expect(page.locator(".answer").last).to_contain_text("You asked: explain tcp")
    expect(page.locator("#history .chat-item")).to_have_count(1)
    assert page.evaluate("() => location.hash").startswith("#/c/")


def test_markdown_tables_lists_maths_and_code(page):
    ask(page, "table please")
    answer = page.locator(".answer").last
    expect(answer.locator("table th")).to_have_count(3)
    expect(answer.locator("blockquote")).to_contain_text("measure before")
    assert answer.locator("ul ul li").count() == 1
    ask(page, "math please")
    math = page.locator(".answer").last
    expect(math.locator(".katex").first).to_be_visible(timeout=15000)  # KaTeX, from the bundle
    assert math.locator(".katex-display").count() == 1
    assert "$5 or $10" in math.inner_text()  # money is not maths
    ask(page, "palindrome function")
    code = page.locator(".codeblock").last
    assert code.locator(".tok-kw").count() >= 3 and code.locator(".tok-com").count() == 1
    code.get_by_role("button", name="Copy code").click()
    assert "def is_palindrome" in page.evaluate("() => navigator.clipboard.readText()")
    page.locator(".bot-tools").last.get_by_role("button", name="Copy answer").click()
    assert "Run it with" in page.evaluate("() => navigator.clipboard.readText()")


def test_html_svg_and_mermaid_previews_are_sandboxed_and_downloadable(page):
    ask(page, "html page")
    block = page.locator(".codeblock").last
    block.get_by_role("button", name=re.compile("Preview")).click()
    frame_el = block.locator("iframe")
    expect(frame_el).to_have_attribute("sandbox", "allow-scripts")
    frame = frame_el.content_frame
    expect(frame.locator("#hi")).to_have_text(
        "Hello from script"
    )  # scripts run, inside the sandbox
    assert page.evaluate("() => document.title") != "blocked"  # the frame can't reach the page
    inner = frame_el.element_handle().content_frame()
    assert (
        inner.evaluate(
            "() => { try { return typeof parent.document.body; } catch (e) { return 'blocked'; } }"
        )
        == "blocked"
    )
    with page.expect_download() as info:
        block.get_by_role("button", name=re.compile("Download preview.html")).click()
    assert info.value.suggested_filename == "preview.html"
    block.get_by_role("button", name=re.compile("Hide preview")).click()
    expect(block.locator("iframe")).to_have_count(0)

    ask(page, "svg circle")
    block = page.locator(".codeblock").last
    block.get_by_role("button", name=re.compile("Preview")).click()
    expect(block.locator("iframe")).to_have_attribute("sandbox", "")  # no scripts at all
    expect(block.locator("iframe").content_frame.locator("circle")).to_be_visible()

    ask(page, "mermaid flow")
    block = page.locator(".codeblock").last
    block.get_by_role("button", name=re.compile("Preview")).click()
    expect(block.locator("iframe").content_frame.locator("svg")).to_be_visible(timeout=30000)
    with page.expect_download() as info:
        block.get_by_role("button", name=re.compile("Download diagram.svg")).click()
    assert info.value.suggested_filename == "diagram.svg"


def test_the_answer_is_ready_at_once_and_a_revision_shows_what_changed(page):
    ask(page, "Write a Python function to revise a list", wait=False)
    bot = page.locator(".msg-bot").last
    # ready while the (slow) check still runs: tools and the background-check chip are shown
    expect(bot.locator(".bg-check")).to_contain_text("Checking in the background", timeout=10000)
    expect(bot.get_by_role("button", name="Copy answer")).to_be_visible()
    expect(bot.locator(".answer")).to_contain_text("Draft that needs fixing")
    finish(page)
    expect(bot.locator(".bg-check")).to_have_count(0)
    expect(bot.locator(".answer")).to_contain_text("Fix from")
    revision = bot.locator(".revision")
    expect(revision).to_contain_text("Revised after the check")
    expect(revision).to_contain_text("needs work")
    revision.locator("summary").click()
    assert revision.locator(".d-add").count() >= 1 and revision.locator(".d-del").count() >= 1
    # the revision is saved with the chat and shown again when it is reopened
    page.reload()
    page.locator("#history .chat-open").first.click()
    expect(page.locator(".msg-bot .revision")).to_contain_text("Revised after the check")


def test_stop_button_ends_a_running_answer(page):
    ask(page, "slow question", wait=False)
    expect(page.locator("#send")).to_have_class(re.compile("stop"))
    expect(page.locator(".answer").last).to_contain_text("Working on it", timeout=15000)
    page.click("#send")
    finish(page)
    expect(page.locator(".stopped")).to_be_visible()
    page.fill("#input", "next")  # the box is usable straight away
    expect(page.locator("#send")).to_be_enabled()


def test_esc_stops_and_regenerate_and_edit_resend(page):
    ask(page, "slow one", wait=False)
    expect(page.locator(".answer").last).to_contain_text("Working on it", timeout=15000)
    page.keyboard.press("Escape")
    finish(page)
    expect(page.locator(".stopped")).to_be_visible()
    ask(page, "first question")
    assert page.locator(".msg-bot").count() == 2
    page.get_by_role("button", name="Regenerate").click()
    finish(page)
    assert page.locator(".msg-bot").count() == 2 and page.locator(".msg-user").count() == 2
    page.locator(".msg-user").last.hover()
    page.locator(".msg-user").last.get_by_role("button", name=re.compile("Edit")).click()
    page.locator(".edit-box textarea").fill("edited question")
    page.get_by_role("button", name="Save and resend").click()
    finish(page)
    expect(page.locator(".msg-user .bubble").last).to_have_text("edited question")
    expect(page.locator(".answer").last).to_contain_text("edited question")
    assert page.locator(".msg-user").count() == 2


def test_history_group_search_rename_pin_export_delete_and_continue(page, site):
    ask(page, "Explain TCP")
    page.click("#new-chat")
    ask(page, "Plan a trip to Jaipur")
    expect(page.locator("#history .chat-item")).to_have_count(2)
    # move one chat back in time: groups are Today, Yesterday, Last 7 days and Older
    now = time.time()
    rows = site.engine.store.query("SELECT id, title FROM chats ORDER BY title")
    site.engine.store.execute(
        "UPDATE chats SET updated_at=? WHERE id=?", (now - 86400 * 1.2, rows[0]["id"])
    )
    page.reload()
    expect(page.locator("#history .group-title")).to_have_count(2)
    titles = page.locator("#history .group-title").all_inner_texts()
    assert [t.lower() for t in titles] == ["today", "yesterday"]
    site.engine.store.execute(
        "UPDATE chats SET updated_at=? WHERE id=?", (now - 86400 * 3, rows[0]["id"])
    )
    page.reload()
    expect(page.locator("#history .group-title")).to_have_count(2)
    assert [t.lower() for t in page.locator("#history .group-title").all_inner_texts()] == [
        "today",
        "last 7 days",
    ]
    site.engine.store.execute(
        "UPDATE chats SET updated_at=? WHERE id=?", (now - 86400 * 30, rows[0]["id"])
    )
    page.reload()
    expect(page.locator("#history .group-title")).to_have_count(2)
    assert [t.lower() for t in page.locator("#history .group-title").all_inner_texts()] == [
        "today",
        "older",
    ]

    page.fill("#search", "jaipur")
    expect(page.locator("#history .chat-item")).to_have_count(1)
    page.fill("#search", "")
    expect(page.locator("#history .chat-item")).to_have_count(2)

    item = page.locator("#history .chat-item", has_text="Plan a trip")
    item.hover()
    item.get_by_role("button", name=re.compile("Options")).click()
    page.get_by_role("menuitem", name="Rename").click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Jaipur plan")
    page.keyboard.press("Enter")
    expect(page.locator("#history .chat-item", has_text="Jaipur plan")).to_be_visible()

    item = page.locator("#history .chat-item", has_text="Explain TCP")
    item.hover()
    item.get_by_role("button", name=re.compile("Options")).click()
    page.get_by_role("menuitem", name="Pin to top").click()
    expect(page.locator("#history .group-title").first).to_have_text(re.compile("pinned", re.I))

    item = page.locator("#history .chat-item", has_text="Jaipur plan")
    item.hover()
    item.get_by_role("button", name=re.compile("Options")).click()
    with page.expect_download() as info:
        page.get_by_role("menuitem", name="Export as Markdown").click()
    assert info.value.suggested_filename == "Jaipur-plan.md"
    text = Path(info.value.path()).read_text(encoding="utf-8")
    assert "## You" in text and "Plan a trip to Jaipur" in text
    item.hover()
    item.get_by_role("button", name=re.compile("Options")).click()
    with page.expect_download() as info:
        page.get_by_role("menuitem", name="Export as JSON").click()
    assert (
        json.loads(Path(info.value.path()).read_text(encoding="utf-8"))["messages"][0]["role"]
        == "user"
    )

    # open the old chat: shown as it was (thinking timeline included), and it can be continued
    page.reload()
    page.locator("#history .chat-item", has_text="Jaipur plan").locator(".chat-open").click()
    expect(page.locator(".msg-user .bubble").first).to_have_text("Plan a trip to Jaipur")
    expect(page.locator(".msg-bot .thinking-head .title")).to_have_text("Thought it through")
    page.click(".thinking-head")
    assert page.locator(".node").count() >= 3 and page.locator(".mchip").count() >= 1
    ask(page, "And what to eat there?")
    assert page.locator(".msg-user").count() == 2
    expect(page.locator("#history .chat-item")).to_have_count(2)

    item = page.locator("#history .chat-item", has_text="Jaipur plan")
    item.hover()
    item.get_by_role("button", name=re.compile("Options")).click()
    page.get_by_role("menuitem", name="Delete").click()
    page.locator("#dlg-confirm").get_by_role("button", name="Delete").click()
    expect(page.locator("#history .chat-item")).to_have_count(1)
    expect(page.locator(".msg-user")).to_have_count(0)  # the open chat was the deleted one


def test_private_mode_saves_and_logs_nothing(page, site):
    page.get_by_role("button", name="Private").click()
    expect(page.locator("#composer-note")).to_contain_text("nothing from this chat is saved")
    ask(page, "my secret plan")
    assert page.locator("#history .chat-item").count() == 0
    assert site.engine.store.query("SELECT 1 FROM chats") == []
    assert site.engine.store.query("SELECT 1 FROM questions WHERE messages LIKE '%secret%'") == []
    page.reload()
    assert page.locator(".msg-user").count() == 0
    page.get_by_role("button", name="Private").click()
    # leaving Private starts saving again, without the private turns
    page.get_by_role("button", name="Auto").click()
    ask(page, "an ordinary question")
    chat = site.engine.store.query("SELECT body FROM chats")[0]["body"]
    assert "ordinary" in chat and "secret" not in chat


def test_attach_text_file_image_drag_drop_and_paste(page):
    page.set_input_files(
        "#composer input[type=file]",
        files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"line one\nbuy milk"}],
    )
    expect(page.locator("#composer-atts .att")).to_contain_text("notes.txt")
    ask(page, "please read the attached notes")
    expect(page.locator(".answer").last).to_contain_text("I read the attachment")
    expect(page.locator(".msg-user .att").first).to_contain_text("notes.txt")
    page.set_input_files(
        "#composer input[type=file]",
        files=[{"name": "dot.png", "mimeType": "image/png", "buffer": PNG_1PX}],
    )
    expect(page.locator("#composer-atts img")).to_have_count(1)
    ask(page, "what is in this picture")
    expect(page.locator(".msg-user img").last).to_be_visible()
    # drag and drop
    page.evaluate("""() => {
      const dt = new DataTransfer(); dt.items.add(new File(["dropped text"], "dropped.md", {type: "text/markdown"}));
      const view = document.querySelector('#chat-view');
      for (const type of ['dragenter', 'dragover', 'drop']) view.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true}));
    }""")
    expect(page.locator("#composer-atts .att")).to_contain_text("dropped.md")
    # paste an image
    page.evaluate("""() => {
      const dt = new DataTransfer(); dt.items.add(new File([new Uint8Array([137,80,78,71])], "pasted.png", {type: "image/png"}));
      document.querySelector('#input').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    }""")
    page.wait_for_timeout(300)
    page.get_by_role("button", name=re.compile("Remove dropped.md")).click()
    expect(page.locator("#composer-atts .att")).to_have_count(
        0
    )  # (the tiny fake PNG can't be decoded, so it is refused)


def test_errors_say_what_happened_and_what_to_do(page):
    ask(page, "boom please")
    box = page.locator(".errbox")
    expect(box).to_be_visible()
    text = box.inner_text().lower()
    assert "try again" in text or "check" in text
    assert box.get_by_role("button").count() >= 1
    assert "traceback" not in text and "internalerror" not in text


def test_keyboard_shortcuts_and_help(page):
    page.keyboard.press("Control+K")
    expect(page.locator("#search")).to_be_focused()
    page.keyboard.press("Escape")
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("/")
    expect(page.locator("#input")).to_be_focused()
    page.fill("#input", "")
    page.locator("#input").blur()
    page.keyboard.press("?")
    expect(page.locator("#dlg-shortcuts")).to_be_visible()
    assert "Ctrl" in page.locator("#shortcut-list").inner_text()
    page.keyboard.press("Escape")
    ask(page, "hello there")
    page.keyboard.press("Control+Shift+O")
    expect(page.locator(".msg-user")).to_have_count(0)
    expect(page.locator("#input")).to_be_focused()


def test_theme_follows_the_system_and_can_be_toggled(browser, site):
    site.reset()
    page = open_page(browser, site, scheme="dark")
    dark_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    assert dark_bg == "rgb(27, 20, 17)"  # dark brown
    page.click("#theme-btn")  # system dark -> light
    assert page.evaluate("() => document.documentElement.dataset.theme") == "light"
    page.wait_for_function(
        "() => getComputedStyle(document.body).backgroundColor === 'rgb(250, 246, 240)'"
    )  # warm off-white
    page.reload()
    assert page.evaluate("() => document.documentElement.dataset.theme") == "light"  # remembered
    page.click("#theme-btn")
    page.click("#theme-btn")  # -> dark -> back to following the system
    assert page.evaluate("() => document.documentElement.dataset.theme || 'system'") == "system"
    page.context.close()


def test_reduced_motion_is_respected(browser, site):
    site.reset()
    page = open_page(browser, site, motion="reduce")
    ask(page, "hello")
    dur = page.evaluate(
        "() => getComputedStyle(document.querySelector('.msg-bot')).animationDuration"
    )
    assert float(dur.replace("s", "")) < 0.01
    page.context.close()


def test_pages_models_usage_keys_developers(page):
    page.get_by_role("link", name="Models").click()
    expect(page.locator("#model-rows tr").first).to_be_visible()
    expect(page.locator("#provider-cards .card").first).to_be_visible()
    page.get_by_role("link", name="Usage").click()
    expect(page.locator("#kpis .tile").first).to_be_visible()
    page.get_by_role("link", name="Keys").click()
    expect(page.locator("#key-list .key-row").first).to_be_visible()
    expect(page.locator("#consent-note")).to_contain_text("saved chats stay on this computer")
    page.get_by_role("link", name="Developers").click()
    expect(page.locator("#snip-curl")).to_contain_text("tempo/auto")
    page.click("#status-pill")
    expect(page.locator("#models-view")).to_be_visible()


def test_delete_my_data_also_deletes_saved_chats(page, site):
    ask(page, "keep me")
    assert site.engine.store.query("SELECT 1 FROM chats")
    page.get_by_role("link", name="Keys").click()
    page.click("#delete-start")
    page.locator("#dlg-confirm").get_by_role("button", name=re.compile("Yes, delete")).click()
    expect(page.locator("#delete-msg")).to_contain_text("saved chats")
    assert site.engine.store.query("SELECT 1 FROM chats") == []


def test_feedback_thumbs_reach_the_server(page, site):
    ask(page, "rate this")
    page.get_by_role("button", name="Good answer").click()
    expect(page.get_by_role("button", name="Good answer")).to_have_attribute("aria-pressed", "true")
    assert site.engine.store.query("SELECT feedback FROM questions")[0]["feedback"] == 1


def test_history_says_when_it_is_only_kept_in_memory(browser, voice_site):
    page = open_page(browser, voice_site)  # this server has no data folder
    expect(page.locator(".side-note")).to_contain_text("Kept in memory only")
    page.context.close()


def test_with_no_model_the_header_and_home_say_so_in_one_line(browser):
    engine, _ = make_engine(env={}, sync_interval_s=0)
    with running(create_app(engine=engine, settings=Settings())) as url:
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(url + "/")
        expect(page.locator("#status-text")).to_have_text("No model yet")
        expect(page.locator("#setup-note")).to_contain_text("run tempo-server setup")
        assert "99" not in page.locator("#status-pill").get_attribute("title")
        context.close()


def test_voice_is_hidden_without_a_key(page):
    expect(page.locator("#mic")).to_be_hidden()
    ask(page, "hello")
    expect(page.get_by_role("button", name="Read aloud")).to_have_count(0)


def test_voice_input_and_read_aloud_with_a_key(browser, voice_site):
    voice_site.reset()
    page = open_page(browser, voice_site)
    expect(page.locator("#mic")).to_be_visible()
    page.click("#mic")
    expect(page.locator("#mic")).to_have_class(re.compile("rec"))
    page.wait_for_timeout(1200)
    page.click("#mic", force=True)  # it pulses while recording
    expect(page.locator("#input")).to_have_value("what is a table", timeout=10000)
    page.press("#input", "Enter")
    finish(page)
    page.get_by_role("button", name="Read aloud").click()
    page.wait_for_timeout(800)
    assert any(c.endswith("/audio/speech") for c in voice_site.calls)
    page.context.close()


@pytest.mark.parametrize("width,height", [(390, 844), (820, 1180)])
def test_small_screens_use_a_drawer_and_never_scroll_sideways(browser, site, width, height):
    site.reset()
    page = open_page(browser, site, width=width, height=height)
    ask(page, "table please")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator("#sidebar")).not_to_be_in_viewport()
    page.click("#open-side")
    expect(page.locator("#sidebar")).to_be_in_viewport()
    expect(page.locator("#history .chat-item")).to_have_count(1)
    page.click("#backdrop", position={"x": width - 5, "y": 300})
    expect(page.locator("#sidebar")).not_to_be_in_viewport()
    page.context.close()


def test_accessibility_basics(page):
    ask(page, "hello")
    assert page.locator("main#main, #main").count() == 1
    assert page.locator("#live").get_attribute("aria-live") == "polite"
    for button in page.locator("button").all():
        name = (
            (button.inner_text() or "").strip()
            or button.get_attribute("aria-label")
            or button.get_attribute("title")
        )
        assert name, button.evaluate("b => b.outerHTML")[:120]
    for field in page.locator("input:visible, textarea:visible, select:visible").all():
        assert field.get_attribute("aria-label") or field.evaluate(
            "f => !!f.labels && f.labels.length"
        ), field.evaluate("f => f.outerHTML")[:120]
    page.keyboard.press("Tab")
    focused = page.evaluate(
        "() => document.activeElement && getComputedStyle(document.activeElement).outlineStyle"
    )
    assert focused != "none"  # a visible focus ring for keyboard users


def contrast(fg, bg):
    def lum(rgb):
        def channel(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (channel(v) for v in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_contrast_meets_wcag_aa_in_both_themes(browser, site, scheme):
    site.reset()
    page = open_page(browser, site, scheme=scheme)
    ask(page, "table please")
    pairs = page.evaluate("""() => {
      const parse = c => { const n = c.match(/[\\d.]+(?:e-?\\d+)?/g).slice(0, 4).map(Number); if (c.startsWith('color(')) { for (let i = 0; i < 3; i++) n[i] = n[i] * 255; if (n.length < 4) n.push(1); } return n; };
      const bgOf = el => { for (let n = el; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c.length < 4 || c[3] > 0.05) if (c[3] !== 0) return c; } return [255, 255, 255, 1]; };
      const out = [];
      for (const el of document.querySelectorAll('body *')) {
        if (!el.childNodes.length || ![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
        const s = getComputedStyle(el); if (s.visibility === 'hidden' || el.offsetParent === null) continue;
        const fg = parse(s.color), bg = bgOf(el);
        out.push({fg: fg.slice(0, 3), bg: bg.slice(0, 3), size: parseFloat(s.fontSize), sel: el.tagName + '.' + el.className, text: el.textContent.trim().slice(0, 30)});
      }
      return out;
    }""")
    bad = [
        p
        for p in pairs
        if contrast(p["fg"], p["bg"]) < (3.0 if p["size"] >= 18 else 4.5)
        and "placeholder" not in p["sel"]
    ]
    assert not bad, bad[:5]
    page.context.close()


def test_every_image_the_docs_pages_show_exists():
    for page in ("index.html", "WEB_UI.md", "../README.md"):
        text = (ROOT / "docs" / page).read_text(encoding="utf-8")
        for name in re.findall(r"screenshots/([\w.-]+\.png)", text):
            assert (SHOTS / name).exists(), f"{page} shows {name}, which is missing"


def test_landing_page_and_demo_share_the_theme_and_work_from_a_file(browser):
    for name, scheme, bg in (
        ("index.html", "light", "rgb(250, 246, 240)"),
        ("demo/index.html", "dark", "rgb(27, 20, 17)"),
    ):
        context = browser.new_context(viewport={"width": 1100, "height": 800}, color_scheme=scheme)
        page = context.new_page()
        problems = []
        page.on("pageerror", lambda e, sink=problems: sink.append(str(e)))
        page.on(
            "console", lambda m, sink=problems: sink.append(m.text) if m.type == "error" else None
        )
        page.goto((ROOT / "docs" / name).as_uri())
        page.wait_for_load_state("load")
        page.wait_for_function(f"() => getComputedStyle(document.body).backgroundColor === '{bg}'")
        assert page.evaluate("() => document.fonts.check('16px \"Inter Variable\"')")
        if name.startswith("demo"):
            expect(page.locator(".thinking-head .title")).to_have_text(
                "Thought it through", timeout=30000
            )
            assert page.locator(".node").count() >= 3 and page.locator(".mchip").count() >= 1
            expect(page.locator(".answer")).not_to_be_empty()
        else:
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        assert not [p for p in problems if "iframe" not in p and "file://" not in p], problems
        context.close()


# ---------------------------------------------------------------------------------------------
# Screenshots at phone, tablet and desktop widths, in both themes (docs/screenshots).

SIZES = {"phone": (390, 844), "tablet": (820, 1180), "desktop": (1440, 900)}


@pytest.fixture(scope="module")
def shots_dir(tmp_path_factory):
    if os.environ.get("TEMPO_SCREENSHOTS") == "1":
        # Overwritten in place: running only some sizes must not delete the others (the
        # landing page shows phone and tablet shots).
        SHOTS.mkdir(parents=True, exist_ok=True)
        return SHOTS
    return tmp_path_factory.mktemp("screenshots")


def seed_history(site):
    """Chats spread over the history groups, written straight to the store."""
    now = time.time()
    day = 86400
    samples = [
        ("Explain TCP versus UDP", "table", 60, True),
        ("Write a palindrome checker", "palindrome", 3600 * 3, False),
        ("Plan a trip to Jaipur", "trip", day * 1.3, False),
        ("What is 17% of 2,340?", "math", day * 4, False),
        ("Draft a polite reminder email", "email", day * 20, False),
    ]
    for i, (title, key, age, pinned) in enumerate(samples):
        messages = [
            {"id": f"u{i}", "role": "user", "content": title},
            {
                "id": f"a{i}",
                "role": "assistant",
                "content": ANSWERS.get(key, "Here is a first draft."),
                "model": "beta/mid",
                "stages": 2,
                "events": [],
            },
        ]
        site.engine.store.execute(
            "INSERT INTO chats (id, user_id, title, pinned, created_at, updated_at, body, search_text) VALUES (?,?,?,?,?,?,?,?)",
            (
                f"seed{i:08d}",
                "local",
                title,
                int(pinned),
                now - age,
                now - age,
                json.dumps({"messages": messages}),
                title.lower(),
            ),
        )


def new_chat(page):
    page.keyboard.press("Control+Shift+O")
    expect(page.locator(".msg-user")).to_have_count(0)


def shoot(page, path):
    page.wait_for_timeout(450)  # let entrance animations settle
    page.screenshot(path=str(path))
    assert path.stat().st_size > 5000


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", list(SIZES))
def test_screenshots(browser, site, shots_dir, size, scheme):
    width, height = SIZES[size]
    site.reset()
    seed_history(site)
    tag = f"{size}-{scheme}"
    page = open_page(browser, site, width=width, height=height, scheme=scheme)
    shoot(page, shots_dir / f"01-home-{tag}.png")

    if size != "desktop":
        page.click("#open-side")
    shoot(page, shots_dir / f"02-history-{tag}.png")
    if size != "desktop":
        page.click("#backdrop", position={"x": width - 5, "y": 300})

    new_chat(page)
    page.fill("#input", "slow explanation of tcp")
    page.press("#input", "Enter")
    expect(page.locator(".mchip").first).to_be_visible(timeout=15000)
    expect(page.locator(".answer").last).to_contain_text("word2", timeout=15000)
    shoot(page, shots_dir / f"03-thinking-timeline-{tag}.png")
    page.click("#send")
    finish(page)

    new_chat(page)
    ask(page, "table please")
    page.click(".thinking-head")
    shoot(page, shots_dir / f"04-answer-{tag}.png")

    ask(page, "html page")
    block = page.locator(".codeblock").last
    block.get_by_role("button", name=re.compile("Preview")).click()
    expect(block.locator("iframe").content_frame.locator("#hi")).to_have_text("Hello from script")
    block.scroll_into_view_if_needed()
    shoot(page, shots_dir / f"05-preview-{tag}.png")

    if size == "desktop":
        new_chat(page)
        ask(page, "math please")
        expect(page.locator(".katex").first).to_be_visible(timeout=15000)
        shoot(page, shots_dir / f"06-maths-{tag}.png")
        for name, file in (
            ("Models", "07-models"),
            ("Usage", "08-usage"),
            ("Keys", "09-keys"),
            ("Developers", "10-developers"),
        ):
            page.get_by_role("link", name=name).click()
            page.wait_for_timeout(500)
            shoot(page, shots_dir / f"{file}-{tag}.png")
        page.keyboard.press("?")
        shoot(page, shots_dir / f"11-shortcuts-{tag}.png")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="New chat").click()
        ask(page, "Write a Python function to revise a list")
        page.locator(".revision summary").last.click()
        page.locator(".revision").last.scroll_into_view_if_needed()
        shoot(page, shots_dir / f"14-revision-{tag}.png")
    for label, name in (("12-landing", "index.html"), ("13-demo", "demo/index.html")):
        docs_page = browser.new_page(
            viewport={"width": width, "height": height}, color_scheme=scheme
        )
        docs_page.goto((ROOT / "docs" / name).as_uri())
        docs_page.wait_for_load_state("load")
        if name.startswith("demo"):
            docs_page.wait_for_selector(".thinking.finished", timeout=30000)
        shoot(docs_page, shots_dir / f"{label}-{tag}.png")
        docs_page.close()
    page.context.close()
