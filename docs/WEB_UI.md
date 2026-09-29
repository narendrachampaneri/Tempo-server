# The web app

`tempo-server serve`, then open http://127.0.0.1:8000. It is plain HTML, CSS and ES modules (no
framework, no build step) in [`tempo/web/`](../tempo/web), and every file it needs ships with
the package: fonts, KaTeX and Mermaid included ([licences](../tempo/web/LICENSES.md)). It makes
no request to any other website, so it works offline with Ollama.

![Desktop, light](screenshots/04-answer-desktop-light.png)

More screenshots (phone, tablet and desktop, light and dark) are in
[`docs/screenshots/`](screenshots). They are made by the browser tests
(`TEMPO_SCREENSHOTS=1 pytest tests/test_web_e2e.py`).

## What is in it

| | |
|---|---|
| **Look** | Warm off-white with coffee-brown accents, and a dark brown theme. It follows your system until you press the theme button, which cycles system, light and dark. Animations respect `prefers-reduced-motion`. Phone, tablet and desktop layouts (the sidebar is a drawer on small screens). |
| **Thinking timeline** | Each step of a question is a node that lights up while it runs, with the models shown as chips the moment they are called (spinner, then a tick, or a cross with the reason). Open **Full log** for the plain event list. A saved chat replays the same timeline. |
| **History** | Chats grouped Today, Yesterday, Last 7 days and Older (pinned first). Search finds words inside chats. Rename, pin, delete, export as Markdown or JSON. Opening an old chat shows it exactly as it was and you can continue it. |
| **Answers** | Streaming with **Stop** (or `Esc`), regenerate, edit and resend a question, copy an answer, copy a code block. Markdown with tables, lists, quotes, maths (KaTeX) and syntax highlighting. |
| **Previews** | Code blocks in `html`, `svg` or `mermaid` get a **Preview** button: a sandboxed frame, a **Download** button and an **Expand** view (see "Previews are sandboxed"). |
| **Attach** | Text and code files (up to 200 KB, five per message) and images (four, shrunk to 1280 px), by button, drag and drop or paste. Images are offered only when a vision model is ready, and only vision models ever receive them. PDFs are not read yet (the app says so). |
| **Voice** | A microphone (speech to text) and a speaker (read aloud) appear only when a speech model is ready for you: today that means a Groq key (`whisper-large-v3` and Orpheus are on Groq's free plan). Without a key they are hidden. |
| **Modes** | Auto, Fast, Best, Private. The ⚙ panel has a model picker (advanced: the model that writes the first draft), the most stages, the time budget and the free-request budget. |
| **Pages** | Models, Usage, Keys and Developers, in the same style. The status badge (top right) shows how many models are ready right now and opens the Models page. |
| **Shortcuts** | `Ctrl/⌘ + Shift + O` new chat, `Ctrl/⌘ + K` search chats, `/` focus the message box, `Esc` stop, `Enter` send, `Shift + Enter` new line, `?` the shortcut panel. |
| **Errors** | Every failure says what happened and what to do next, with a button where one helps (add a key, see models, try again, enter an API key). |

## Where your chats live, and what "private" means

- Chats are stored **only on your computer**, in the database in the data folder
  (`tempo.db`, the same place as everything else Tempo keeps; see the README). If the server
  runs with `TEMPO_DATA_DIR=memory`, the sidebar says history is kept in memory only.
- Saving history is **not consent to train**. Saved chats are a separate table
  ([`tempo/history.py`](../tempo/history.py)); no export reads it. Training use stays a
  separate opt-in (Keys page, "Your data"), and "Delete my data" removes chats too.
- **Private** mode sends only to local models (Ollama) and saves nothing: no chat in history,
  no question in the log, no answer in the cache. Reloading the page forgets the conversation.
  If you switch back to another mode in the same chat, only the non-private messages are saved.
- Each user of a shared server sees only their own chats.

## Previews are sandboxed

Model-written HTML can do anything, so previews run in an `iframe` with an opaque origin: no
access to the page, its storage or the API. A content-security-policy inside the frame blocks
every network request (so a preview cannot phone home). HTML previews may run scripts; SVG and
Mermaid previews cannot. Mermaid is drawn by the bundled Mermaid library (strict mode) and
shown as an image in the same kind of frame. The Download button saves the HTML, or the SVG.

## Speed

On the development machine (offline, localhost) a first load takes about 80 ms to the `load` event and 70 ms to first paint (median of five runs), in 19 small requests and about 210 KB, most of it two fonts. The browser test asserts a first load under a second and under 500 KB. Larger pieces load only when used: KaTeX (when an answer has
a formula), Mermaid (when you press Preview on a diagram), the Gujarati and Devanagari fonts
(when a page contains those scripts).

## Accessibility

Keyboard use throughout (menus open with the keyboard and arrow keys move in them), visible
focus rings, labelled controls, a polite live region that announces "thinking" and "answer
ready" without reading every streamed word, a closed drawer that keyboard focus cannot reach,
and text that meets WCAG AA contrast in both themes (a browser test measures it).

## For developers

- Server side: `tempo/history.py` and `/api/chats*`, `tempo/speech.py` and `/api/speech/*`,
  `/api/capabilities`, `/api/ask` with `save: false`, and `/static` (fonts and libraries are
  cached; code is revalidated).
- Browser tests: `pip install -e ".[dev,e2e]" && playwright install chromium`, then
  `pytest tests/test_web_e2e.py`. They start a real server with scripted models. Without
  Playwright they are skipped; CI sets `TEMPO_REQUIRE_E2E=1`.
- `docs/index.html` and `docs/demo/index.html` (GitHub Pages) use copies of the theme and fonts
  in `docs/assets/`; a test fails when a copy is out of date and says how to refresh it.
