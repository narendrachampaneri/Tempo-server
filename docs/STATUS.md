# Status

_Last updated 2026-09-30. Everything is on `main` (steps 10 and 11, the pre-public check, and
the repo tidy below step 11). One workflow from now on: one step = one branch = one pull request
into `main`, merged when CI is green on Linux, Windows and macOS (CLAUDE.md rules 11–14). The
other branches hold nothing that isn't on `main` and wait for the owner to delete them. GitHub
Actions starts no jobs until the Actions minutes reset on 1 October; then the full CI runs on
`main` ("Next")._

Read [CLAUDE.md](../CLAUDE.md) first: it has the rules every session follows.

## Done

### Step 1 (setup and checks)

- Rules saved in `CLAUDE.md`. Full test suite and a full mock run (CLI and web page) of a
  greeting, a coding task, a maths problem, a long document and Gujarati/Hindi questions.
  Fixed: maths word problems classified as chat, wrong-language answers passing, a misleading
  vault warning, and sync guesses (reasoning from the organisation's name).

### Step 2: the owner's decisions on step 1

- **Cerebras** is off by default (`enabled: false`; turn on with
  `TEMPO_ENABLE_PROVIDERS=cerebras`), marked "trial, needs a payment method", and blocked in code
  for `tempo-server eval` and `tempo-server collect` (`blocked_for: [eval, collect]`).
- **Training data "yes" sources**: local Apache-2.0 or MIT models through Ollama (Tempo reads
  each installed model's licence from `/api/show`), public datasets with open licences
  (questions only), and the owner's own 👍/👎. Groq and OpenRouter stay "unclear". Every logged
  call records the model's licence and verdict; every exported row has `factors.source`
  (dataset and licence, or `tempo-traffic`) and `factors.output_terms` (verdict and licence per
  model). `tempo-server collect --yes-only` uses only "yes" models for every stage, judge included;
  collect and export work with local-only runs, and the estimate counts local models.
- **Previews** (preview, alpha, beta, experimental, stealth) are marked, always rank below
  stable models, never judge, and are dropped when they expire. **Specialists** (finance,
  health) answer only questions the analyzer puts in their field.
- **Language check**: fails only when most of the answer is in another language or script;
  code, names, numbers, links and capitalised technical terms don't count; a language the user
  asks for ("answer in Hindi", "ગુજરાતીમાં જવાબ આપો") wins. Works for every script, and for
  English, Spanish, French, German, Italian, Portuguese and Dutch by function words.
- **Code check**: no code block fails only when the user clearly asked for code (write, fix,
  implement, convert, also in Hindi and Gujarati); otherwise the judge decides.
- **Rule 3 gap**: every hand-entered limit in `models.yaml` has `limits_source` and
  `limits_checked` (on the model or its provider); a test enforces it.

### Step 2: live catalog and new providers

1. **OpenRouter health**: every free model (both prices 0, with or without `:free`) gets its
   `/endpoints` read during sync, at most every 15 minutes per model (the server checks between
   its 6-hourly full syncs). No endpoints: dropped. Best endpoint status below 0 or under 95%
   success in 30 minutes: degraded, ranked down. `openrouter/free` is kept only as the last
   fallback. The owner's six test models (gpt-oss-120b, llama-3.3-70b-instruct, qwen3-coder,
   qwen3.6-plus, glm-4.5-air, deepseek-r1-0528, all `:free`) are excluded in a test. With a key,
   the free daily limit is read live from `GET /api/v1/key`.
2. **Model types**: chat, code, vision, speech-to-text, text-to-speech, safety, embedding,
   reranker, decision; only chat, code and vision get chat requests. Groq's free plan is
   registered by type: gpt-oss-120b, gpt-oss-20b, qwen3.8-27b (chat); gpt-oss-safeguard-20b,
   llama-prompt-guard-2-86m/22m (safety); whisper-large-v3 and -turbo (speech-to-text);
   orpheus-v1-english and orpheus-arabic-saudi (text-to-speech), with limits from
   console.groq.com/docs/rate-limits (checked 2026-09-28).
3. **New providers**, each with its own live model list, limits from responses where possible,
   and terms verdict, exact sentences and link (all nine quotes verified on the live pages with
   `tempo-server terms --check` on 2026-09-28):
   - Cloudflare Workers AI (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`): 10,000 neurons a
     day; the seven paid-only models are skipped.
   - NVIDIA API catalog: public model list read for real (80 usable of 81 listed); off by
     default because its trial terms allow testing and evaluation only.
   - Cohere trial: 1,000 calls a month counted, 20 a minute per chat model; blocked for eval
     (terms forbid benchmarking).
   - Mistral free plan: limits learned from response headers (minute and month).
   - OpenCode Zen: public model list; free models only; each user's own key only (a server key
     is ignored); per-model data policy from Zen's privacy section.
   - **GitHub Models: not built.** GitHub retired it on 30 July 2026 (docs.github.com/en/
     github-models: "The playground, model catalog, inference API, and bring your own key (BYOK)
     are no longer available to any customer").
   All new providers call through LiteLLM's OpenAI-compatible route (tested against a local fake
   server). Limits: `x-ratelimit-limit-*` headers replace hand-entered limits (source
   `live: response headers`, dated); monthly pools are counted.
4. **`tempo-server models --free [--json] [--all]`**: live table of provider, model, type, context, max
   output, inputs, tools, limits, data policy, health, last check and status. It reads every
   list first and saves the catalog to `catalog.json` in the data directory, which one-shot CLI
   runs load.
5. **Data policy** per provider and model (`ok`, `may-log`, `may-train`), with the sentence and
   link. Privacy `no_logging` (API) or `--no-logging` (CLI) never reaches flagged models:
   Google's free tier, NVIDIA's trial, Cohere, Mistral's free plan, stealth models and Zen's
   Big Pickle, MiMo, Ling Fin, Nemotron and Space Bunny.
6. **Ollama licences** recorded per model; Apache-2.0 and MIT count as "yes";
   `tempo-server collect --yes-only`.
7. **[docs/COLLECT_ANYWHERE.md](./COLLECT_ANYWHERE.md)**: exact Windows steps (local
   Apache-2.0 models from two families, Task Scheduler) and a scheduled GitHub Actions job
   ([docs/examples/collect-workflow.yml](./examples/collect-workflow.yml), not active) that
   resumes across runs through the cached data directory. `tempo-server collect --minutes N` added.
- Also fixed: a question whose answers all failed ended with "answer passed its check"; it now
  ends with `not_passed` ("no answer passed its check; sent the best one").
- Also fixed (rule 4): stored keys were shown by their last four characters in `tempo-server keys
  list`, `tempo-server keys add`, the API and the Keys page. They now show a one-way fingerprint
  (`fp:1a2b3c4d`) that reveals no part of the key; old rows are upgraded when listed. And a key
  for a provider with a public model list (OpenRouter, NVIDIA, Zen) was "verified" by a list
  that accepts any key: OpenRouter keys are now checked with `/api/v1/key`, the others report
  "could not verify".
- Tests: 267 pass, lint clean. Mock run through the CLI and the web page repeated after the
  changes: stage events, fallback after the demo rate limit, cool-down, fixes, and the language
  check on Gujarati and Hindi (the English-only demo models now correctly fail it).

### Step 3: the owner's decisions on step 2

- **NVIDIA**: off by default and owner-only (`owner_only`): the local user or the admin may turn
  it on for private testing; other users and demo mode are refused. Never in collect or exports.
- **Cohere**: only users' own trial keys (`byok_only`); it only answers: never the judge, never
  eval, collect or exports (`blocked_for: [eval, collect, judge, export]`). Its model list is
  read with a user's key when they add it and at each sync.
- **Mistral**: text outputs are "yes" (terms 3.3 restrict image outputs only; quote and link
  kept). Flagged "may-train" until the owner opts out in the console and sets
  `TEMPO_OPTED_OUT=mistral`; then "may-log" (no written zero-retention), and only for requests
  on the server's key.
- **Cloudflare**: its Service-Specific Terms (last updated 2026-09-28) say "you retain all
  applicable intellectual property or other proprietary rights in Inputs and Outputs" and defer
  to each model's terms, so `licence_decides`: Apache-2.0/MIT models are "yes", others follow
  their licence, unknown licences stay "unclear". Licences are read from Cloudflare's list.
- **Space Bunny**: Zen's version is no longer flagged (Zen states zero retention) and is marked
  "maker not disclosed"; OpenRouter's stealth version stays "may-log".
- **OpenRouter**: every free model is "may-log" unless a model's provider has a written
  no-retention policy, recorded with its source (a test enforces the source).
- **Zen**: off by default (`TEMPO_ENABLE_PROVIDERS=opencode`), users' own keys, answering only;
  never eval, collect or exports.
- **Terms before every export**: `tempo-server export-laya`, `export-sft` and `export-pairs` first
  re-read the terms of hosted "yes" providers that appear in the log; a provider whose quotes
  changed or whose page can't be read is left out of that export, with the reason printed.
- **GitHub Actions collect job**: not activated (owner: first a local run with an Apache-2.0
  model). The example stays in docs/examples.

### Step 3: Tempo's own models and publishing (plan and data only)

- **ARCHITECTURE.md §14 "Tempo models"** and **[TEMPO_MODELS.md](./TEMPO_MODELS.md)**:
  Tempo-Router and Tempo-Judge (Laya, Apache-2.0), Tempo-Core (a 1–4B Apache-2.0/MIT model;
  first choice Qwen3-1.7B, licences checked on Hugging Face), Tempo Tune add-ons. Training plan
  per model (LoRA/QLoRA SFT, then DPO on pairs, later GRPO with rewards from maths and sandboxed
  code checks) on a free Kaggle notebook, with estimates to be measured; shipped as GGUF Q4_K_M
  (Tempo-Core) or Laya checkpoints (PyTorch fp32). Promotion gate, model-collapse protection
  and the Hugging Face release plan.
- **`tempo-server export-sft`** (conversation → the final answer that passed its check) and
  **`tempo-server export-pairs`** (chosen = that answer, rejected = an earlier answer that failed,
  hard failures first), in TRL's formats. Only "yes" rows, the licence and source on every row,
  no 👎, only the owner's and `tempo-server collect`'s questions by default (`--user` adds a consenting
  user), the same held-out split as every export, and a repetition measure for the collapse
  check. 7 tests.
- **[USE_CASES.md](./USE_CASES.md)**: the 20 scenarios, each with an example, what Tempo still
  needs and its roadmap phase.
- **[PUBLISHING.md](./PUBLISHING.md)**, **CONTRIBUTING.md**, **SECURITY.md**, **examples/**
  (tested against demo mode), and the README now opens with "open models plus the system that
  runs and trains them" and carries the provider terms table. A scan of every tracked file
  found no keys.
- Tests: 281 pass, lint clean.

### Step 4: the owner's decisions on step 3

- **Licence**: Apache-2.0. `LICENSE` ("Copyright 2026 The Tempo-server authors"), `NOTICE`
  (credits Laya), and `pyproject.toml` metadata. Dataset credits (for example MBPP's CC-BY-4.0
  attribution) go in the model cards (TEMPO_MODELS.md §5).
- **Tempo-Core**: Qwen3-1.7B first, Granite 3.3 2B as the second family; a 4B option only if it
  reaches 8 tokens a second on 4 CPU threads and wins on the held-out set (plan).
- **Consent**: per user, opt-in, off by default, withdrawable, every change logged
  (`tempo-server users consent NAME --on/--off`, `GET`/`PUT /api/consent`). Consenting users'
  questions join every export (Laya, SFT, pairs); text from Tempo's own traffic is scrubbed
  first (emails, phone numbers including Indian mobiles, card, Aadhaar and PAN numbers, IP
  addresses). Users delete their data with `tempo-server users forget NAME` or `DELETE /api/data`.
- **Dolly**: out of every training export; its rows go only to test splits.
  OpenAssistant `oasst2` (Apache-2.0) proposed as the replacement (not added yet).
- **Data mix**: `TEMPO_MIN_PUBLIC_SHARE=0.3`, `TEMPO_MAX_SELF_SHARE=0.3`; exports report the mix
  and warn.
- **Promotion gate per task type** (plan): a new version takes a task type only with 30+
  held-out questions and no drop; other types keep the old version.
- **Demo**: a replayed recording on a static page (plan). **Name**: Tempo-server in the README,
  `LICENSE`, package metadata, Docker image and Hugging Face placeholders.

### Step 4: OpenAI API compatibility

- **Tool calling on every provider** (`tempo/compat.py`): native `tools` for models whose
  provider lists tool support; for every other model the tools are described in a system
  message. Replies in the text formats open models print (Hermes/Qwen `<tool_call>`, Mistral
  `[TOOL_CALLS]`, Llama `<|python_tag|>` and `<function=…>`, fenced or bare JSON) become the
  same OpenAI `tool_calls`. Every call is validated (known function, JSON-object arguments
  matching its schema, `tool_choice` `auto`/`none`/`required`/named, `parallel_tool_calls`);
  a bad reply is retried on another model, and the error names the reason if none succeeds.
  Tool results (`role: "tool"`) are sent back (as text for emulated models).
- **Strict JSON schema** (`response_format` `json_schema` or `json_object`): schema in the
  system message, the answer's JSON extracted and validated with `jsonschema`, a mismatch
  retried on another model, clean JSON returned; the normal check/fix stages still run.
- **Images**: `image_url` parts go only to vision models, at every stage (tested end to end,
  and the image reaches the provider unchanged through LiteLLM).
- **Streaming** for all of them: tool calls stream as OpenAI `tool_calls` deltas; tool-call and
  JSON answers are streamed after validation.
- **Malformed requests** get 400s like OpenAI's (unknown `tool_choice` function, `required`
  without tools, `json_schema` without a schema).
- **Real client**: the official OpenAI Python SDK against a live Tempo-server over HTTP in demo
  mode (`tests/test_real_client.py`): chat, streaming, tool calls native and emulated with
  tool results, strict JSON streamed and not, and an image. Also LiteLLM's real streaming of
  tool-call fragments against a fake provider server. Demo models now answer tools, JSON and
  images, and `examples/tools_and_json.py` shows all three.
- Also fixed: NVIDIA's "never in demo mode" now also holds when demo models are loaded in code,
  not only through `TEMPO_ENABLE_MOCK`.
- Tests: 320 pass, lint clean.

### Step 5: the owner's decisions on step 4

- **Structured outputs**: `response_format` is passed to models whose provider lists structured
  outputs (OpenRouter's `supported_parameters`, Groq's and Google's docs); every answer is still
  validated. A provider that rejects the parameter is remembered and never sent it again.
- **Tool follow-ups** (`TEMPO_TOOL_FOLLOWUP`, default `quick`): the text answer after a tool
  result always gets the quick checks (empty, refusal, wrong language); the judge and fix stages
  run only in `best` mode or when a quick check fails (`full` always judges, `off` none).
- **Groq and Google capabilities** marked per model from their official docs, with source and
  date (console.groq.com/docs/tool-use, /vision, /structured-outputs; ai.google.dev model page;
  checked 2026-09-28). Models whose docs weren't clear stay unmarked (Gemma's vision flag
  removed). A keyed sync overwrites them.
- **Web page**: a consent switch and a two-step "Delete my data" on the Keys page.
- **oasst2** in `tempo-server collect`: first user turns only, deleted/flagged/synthetic/toxic
  messages skipped, scrubbed, Apache-2.0 on every row (57,904 of 64,592 prompts kept).
- **Error codes**: 503 with `Retry-After` (a minute, or the next daily reset) only when no model
  is available or every free quota is used up; 502 with the reason when models answered but
  none gave valid JSON or a valid tool call.
- **Scrubbing**: US Social Security numbers (`[ssn]`) and IBANs (`[iban]`, mod-97 checked).

### Step 5: easy for other people

- **`tempo-server setup`** (`tempo/setup.py`): walks through Ollama, Groq, Google, OpenRouter,
  Cloudflare (asks the account id), Mistral, Cohere, OpenCode Zen, NVIDIA and Cerebras: where to
  get the key, free limits with source and date, training verdict and data policy. Keys are
  checked with the provider (`verify_key`), stored encrypted for the owner only, shown as a
  fingerprint. Rules applied: Cerebras stays off (no key asked); NVIDIA only after confirming
  "own private testing"; OpenCode Zen opt-in with the user's own key; Cohere only the user's own
  key. Non-secret settings (`CLOUDFLARE_ACCOUNT_ID`, `OLLAMA_API_BASE`,
  `TEMPO_ENABLE_PROVIDERS`) go to `<data dir>/settings.env`, which refuses secret-looking names.
  Then the live lists are read and it prints free requests a day per provider and the total.
  The admin (`TEMPO_API_KEY`) also gets the owner's setup keys; other users never do.
- **Quota view**: `tempo-server quota [--json]`, `GET /api/quota`, and a strip on the Ask page with
  free requests left today per provider (resets in …). When every free quota is used up, local
  Ollama models (after the cache) answer and the thinking window says so; with no local model
  the 503 says to start Ollama or wait (Retry-After).
- **Local first** (`TEMPO_LOCAL_FIRST=auto`, `TEMPO_LOCAL_FIRST_MAX_COMPLEXITY=0.3`): simple
  questions go to a running Ollama model first; never in `best` or with tools/JSON/an explicit
  model.
- **Packaging**: `tempo-server` console command (plus `tempo`), wheel includes `tempo/data`,
  SPDX licence metadata; the wheel installs and answers in a clean venv. `Dockerfile`
  (python:3.12-slim, non-root uid 10001, `/data` volume, health check, `BASE` build arg for a
  Docker Hub mirror); built and run here (627 MB, mostly LiteLLM). Workflows: `ci.yml` (tests,
  lint, package build on every push; green on GitHub) and `docker-publish.yml` (GHCR; only on a
  published release or a manual run, tests first, amd64 + arm64). **Nothing published.**
- **Name checks** (2026-09-28): PyPI `tempo-server`, `tempo_server`, `temposerver` are free
  (404); PyPI `tempo` is taken by an unrelated project. Hugging Face: no model or dataset named
  tempo-server, user/org `tempo-server` free; one unrelated Space
  (`kokluch/tempo-edf-mcp-server`). **Clash:** Grafana Tempo builds binaries named `tempo`,
  `tempo-query`, `tempo-cli` and `tempo-vulture`, and its `grafana/tempo` image has 100M+ pulls,
  so the docs and Docker use `tempo-server`; `tempo` stays as a convenience alias.
- **docs/CONNECT.md**: Open WebUI, LibreChat, Continue, Aider, OpenCode, n8n, LangChain (Python
  and JS), the OpenAI SDK, which key to use, and a troubleshooting table.
  `tests/test_connect.py` runs the OpenAI SDK against a real server with a user's Tempo key
  (models, chat, streaming) and checks a wrong key is refused.
- **Public demo**: `tempo-server record-demo` saves the engine's events for a few questions to
  `docs/demo/recording.js`; `docs/demo/index.html` replays them (thinking window, stage dots,
  streamed answer, final meta; speed, skip, dark mode, phone width). Recorded in demo mode and
  labelled so on the page; works from `file://` and GitHub Pages.
- **README quick start**: install, `tempo-server setup`, `tempo-server serve`, connect an app;
  Docker; the name clash.
- Tests: 344 pass, lint clean.

### Step 6: the owner's decisions on step 5

- **`.env` keys are the owner's**: other users of a server use only their own keys unless
  `TEMPO_SHARE_SERVER_KEYS=1`. The owner's own jobs (`collect`) count as the owner. Quota buckets
  are per key (a one-way fingerprint), so one key used by the owner, the admin and collect is
  counted once (before, the same vault key had one bucket per user).
- **`tempo` alias** kept for 0.x with a notice that it goes away before 1.0; `tempo-server` in
  every doc, hint and message.
- **PyPI**: `pypi-publish.yml` with Trusted Publishing (no token stored); runs on a published
  release (PyPI) or by hand (TestPyPI). The owner creates the Hugging Face organisation.
- **Image size** 627 MB accepted for 0.1; a slimmer image is a later task (Next).
- **Local first** only when Ollama reported the model as installed, with a note in the thinking
  window ("Local first: … TEMPO_LOCAL_FIRST=off to turn off").
- **Landing page** `docs/index.html` for GitHub Pages from `/docs` on `main`: what Tempo-server
  is, install per system, the demo, links (`docs/.nojekyll` so Pages serves it as is).

### Step 6: works on Windows, macOS and Linux

- **CI** (`.github/workflows/ci.yml`): lint, Linux tests on Python 3.11, 3.12, 3.13 and 3.14 and a
  Linux install test on every push; Windows (3.11–3.14), macOS (3.13) and install tests on all
  three systems on pull requests, releases and manual runs. Results: see "CI results" below.
- **Install tests** on each system: `install.sh` / `install.ps1` (installing uv themselves),
  the installers with pipx, `pipx install` and `uv tool install` of the built wheel; each then
  runs `tempo-server --version`, `setup --non-interactive`, a demo question and `doctor --offline`.
- **Fixed for other systems**:
  - data folder per system (`%LOCALAPPDATA%\tempo-server`, `~/Library/Application
    Support/tempo-server`, `$XDG_DATA_HOME/tempo-server`), with a safe one-time move of an old
    `~/.tempo` (rename, or copy → swap → delete across drives; never over a folder in use);
  - every text file read and written as UTF-8 (ruff `PLW1514` enforces it where it can see);
  - console output switched to UTF-8 when Windows gives a legacy code page (▸, ✗, Gujarati);
  - `tzdata` on Windows: it has no IANA time zones, so Google's midnight-Pacific reset failed to
    load (found by the first Windows CI run: 56 tests);
  - line endings: `.gitattributes` keeps LF (CRLF for `.ps1`);
  - file permissions: the data folder is created 0700 and the secret 0600 on POSIX; the test
    for it runs only on POSIX (Windows keeps profile folders private with ACLs);
  - tests get a private home folder, so nothing reads or moves the real one;
  - asyncio: nothing POSIX-only (no signal handlers); the default Windows event loop works.
- **Professional install**: `install.sh` and `install.ps1` (uv if present, else pipx if present,
  else install uv; tool Python pinned to 3.12 unless `TEMPO_SERVER_PYTHON`; setup on the terminal
  even when piped, else non-interactive). README install per system (one line, pipx/uv, Docker,
  the GitHub archive URL until PyPI); venv and editable installs moved to CONTRIBUTING.md.
- **`tempo-server doctor`**: Python version, the command on PATH, the data folder (writable, old
  folder left, secret file mode), keys (vault and environment, fingerprints only), which
  providers are reachable (a plain request without a key), Ollama, and the port; a fix for each
  problem; exit code 1 when something must be fixed.
- Also: `tempo-server --version`, `setup --non-interactive`, Python 3.11–3.14 classifiers,
  project URLs, `twine check --strict` in CI.
- Tests: 361 pass locally (Linux, Python 3.11 and 3.13), lint clean.

**Owner's decisions on step 6** (2026-09-29): Windows and macOS stay on pull requests and
releases while the repository is private; once it is public they also run on every push to
`main` (the workflow checks `github.event.repository.private`, so this switches on by itself).
The installers keep Python 3.12 for 0.1.0.

**Secret scan of the full git history** (2026-09-29, before the repository goes public): no keys
or secrets found.
- gitleaks 8.28.0 over every commit on every branch (`--log-opts=--all`, 57 commits, ~1.4 MB):
  no leaks.
- A second pass over every added line in every commit for the key formats of Tempo's providers
  and common services (Groq `gsk_`, Google `AIza`, OpenRouter `sk-or-v1-`, OpenAI-style `sk-`,
  NVIDIA `nvapi-`, Cerebras `csk-`, Hugging Face `hf_`, GitHub tokens, Slack, AWS, private-key
  blocks) and for non-empty `*_API_KEY` / `*_TOKEN` / `*_SECRET_KEY` assignments: nothing.
- No `.env`, key file, database, `settings.env` or `catalog.json` was ever committed (only
  `.env.example`, whose values were always empty; one early version had comments after `=`,
  fixed in step 1).
- Only example email addresses (`@example.com`, `a@b.com`); every commit is authored by
  `Claude <noreply@anthropic.com>`; the owner's email appears nowhere.
- Keep GitHub secret scanning and push protection on once public (PUBLISHING.md §2).

**CI results** (manual run 11, commit `8efeee1`, 2026-09-29; all green):

| System | Tests | Install test |
|---|---|---|
| Linux (ubuntu-latest) | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | install.sh (uv) ✓, install.sh (pipx) ✓, pipx ✓, uv ✓ |
| Windows (windows-latest) | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | install.ps1 (installs uv) ✓, install.ps1 (pipx) ✓, pipx ✓, uv ✓ |
| macOS (macos-latest) | 3.13 ✓ | install.sh (uv) ✓, install.sh (pipx) ✓, pipx ✓, uv ✓ |

Lint and the package build (`twine check --strict`) pass. The first Windows run failed (56
tests and `install.ps1`'s setup step) on the missing time-zone database; `tzdata` fixed it.

### Step 7: MCP server and SDKs

- **MCP server** (`tempo/mcp_server.py`, official MCP Python SDK 2.2, MIT; now a core
  dependency): `tempo-server mcp` (stdio, for desktop apps; the caller is the local owner) and
  `tempo-server mcp --http [--host] [--port 8001]` (Streamable HTTP at `/mcp`; every request
  needs a Tempo key, else 401; each caller uses their own provider keys; it refuses to start when
  no key exists yet). Tools:
  - `ask(question, mode auto/fast/best, private, local_only)`: the answer, the model, every model
    used, the check results, stages, stop reason;
  - `second_opinion(question, private)`: two different families answer; a third family (when
    there is one) lists agreements and differences as strict JSON;
  - `verify(question, answer, answer_model)`: quick checks plus a judge from a different family
    than the answer's: pass/fail, score 0–10, problems;
  - `models(include_all)`: the live free-model list with health and "ready for you";
  - `quota()`: free requests left today.
  They run through the engine (`tempo/assist.py`), so quota, fallbacks, health, privacy and each
  caller's own keys apply. New run option `exclude_families` (router skips those families; such
  runs bypass the cache).
- **Tested with the SDK's own client** (`tests/test_mcp.py`): in-process, over HTTP (user key and
  admin key accepted; no key and a wrong key get 401) and over stdio (`python -m tempo.cli mcp` as
  a real subprocess).
- **docs/MCP.md**: Claude Desktop (macOS, Windows; no official Linux build), Claude Code (one
  command, stdio or HTTP), Cursor and VS Code (Windows, macOS, Linux), full paths for GUI apps,
  HTTP with the key from the environment or a secure prompt, troubleshooting. A test parses every
  JSON snippet.
- **Python SDK** `sdk/python` (`tempo-server-client`, import `tempo_server_client`; httpx only;
  Python 3.9+; sync and async): `stream()` (live thinking-window events), `ask()` (folded
  `Answer`), `feedback()`, `models()`, `quota()`, `me()`, `health()`, `consent()`,
  `set_consent()`, `delete_my_data()`; `TempoError` with status, code and `retry_after`.
  Localhost traffic never goes through a proxy.
- **JavaScript/TypeScript SDK** `sdk/js` (`tempo-server-client`; fetch, no dependencies, types;
  Node 18+ and browsers; ESM): the same methods (`setConsent`, `deleteMyData`, `retryAfter`),
  `stream()` as an async iterator with an `AbortSignal`. Built with TypeScript 5.9 (strict).
- **SDK tests** against a real demo-mode server with a real user key (`sdk/python/tests`, also in
  the main `pytest` run; `sdk/js/test` with `node --test`). CI job `sdk` installs Tempo-server and
  the Python SDK as users get them, runs both suites, and checks both packages (`twine check`,
  `npm pack --dry-run`); Node 22 on every system and Node 20 on Linux.
- **Names** (checked 2026-09-29): `tempo-server-client` is free on PyPI and npm; `tempo-client`
  is taken on both; the npm scope `@tempo-server` is free. Nothing published.
- **Examples** `examples/mcp_config.json`, `sdk_python.py`, `sdk_js.mjs` (both SDK examples run
  against a demo server); README "Use it from any AI assistant"; landing-page links;
  ARCHITECTURE §6.2 and USE_CASES #17 updated.
- Tests: 376 pass locally (Linux, Python 3.11), JS 6 pass (Node 22), lint clean.

**CI results, step 7** (manual run 21 on `main`, commit `fb3fea6`, 2026-09-29):

| System | Server tests (incl. MCP and Python SDK) | SDK job (Python SDK + JS SDK, packages) | Install tests |
|---|---|---|---|
| Linux | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓, Node 20 ✓ | ✓ |
| Windows | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓ | ✓ |
| macOS | 3.13 ✓ | Node 22 ✓ | ✓ |

The only failure in that run was lint: `ruff format --check` also formats Python code blocks in
Markdown and docstrings (README, `examples/sdk_python.py`). Fixed in `0d58022`; its push run
(lint and Linux) is green.

### Step 8: the owner's decisions on step 7

1. **SDK publishing**: `.github/workflows/sdk-publish.yml` publishes `tempo-server-client` to PyPI
   (Trusted Publishing, environment `pypi-sdk`) and npm (npm Trusted Publishing, environment
   `npm`, npm ≥ 11.5.1, provenance added by npm), only when a GitHub release is published; tests
   first, and the tag must match both SDK versions. Nothing published.
2. **MCP over HTTP** keeps requiring a key, even on 127.0.0.1 (no change).
3. **`TEMPO_CORS_ORIGINS`**: an exact list of websites allowed to call the API from a browser; off
   by default; a wildcard, a path or anything but an `http(s)://host[:port]` origin stops the
   server from starting. No credentials; `Retry-After` exposed (`tests/test_cors.py`).
4. **MCP questions** are logged with `source = mcp` (a new column) and kept out of `export-sft`,
   `export-pairs` and `export-laya`, which report how many they skipped; `TEMPO_TRAIN_ON_MCP=1`
   includes them.
5. The MCP SDK stays in the core install; 6. TypeScript stays on 5.9 for 0.1.0 (no change).

### Step 8: checking code and maths answers by running them

- **Research and choice** (details in [SANDBOX.md](./SANDBOX.md)): **Wasmtime** (`pip install
  wasmtime`, Bytecode Alliance, monthly releases, wheels for Windows/macOS/Linux on x86-64 and
  ARM64, Python 3.9–3.14) running **CPython 3.14.5 for WASI** (Brett Cannon's builds; PSF) and
  **QuickJS-ng 0.17.0** (`qjs-wasi.wasm`; MIT). Rejected: Pyodide (needs a JS runtime),
  MicroPython (not CPython), `llm-wasm-sandbox` (old Wasmtime pin, generic top-level package
  names, one maintainer), Docker/gVisor/seccomp (not allowed or not on Windows/macOS),
  RestrictedPython (not a security boundary).
- **`tempo/sandbox.py`**: `tempo-server sandbox install` downloads the two runtimes once
  (SHA-256 pinned; unsafe archive paths refused), unpacks them in `<data>/sandbox`
  (`TEMPO_SANDBOX_HOME`), compiles them for the machine (rebuilt if copied from another). Each run:
  a fresh instance and engine, its own temporary folder (`/work`), the standard library
  read-only, no network, no processes, no host environment; wall-clock limit by epoch
  interruption; memory cap; a watchdog stops runaway output and disk use; at most two runs at
  once. Output goes to files (Wasmtime's Python callbacks for output panic at interpreter exit,
  so they are not used).
- **`tempo/execute.py`**: code answers (Python or JavaScript) run with tests from the question
  and answer (`assert`, `test_*`, unittest, a pytest stand-in with `raises`/`approx`/
  `parametrize`, solution modules the tests import). Results: passed, failed (with the error for
  the fix stage) or inconclusive (third-party packages, input, network, or a limit hit without
  tests). Maths: rules for arithmetic ("17% of 2,340", "(3.5 + 2) * 4", square roots, powers),
  else a model-written program (once per question, one free request; `TEMPO_SANDBOX_MATH=rules`
  never asks), compared with the answer's final number allowing for rounding.
- **In the pipeline**: the check stage runs them ("+ sandbox"); a failed run is a hard failure
  (code, or maths by rules) or a strong penalty (maths by a model's program), and its error goes
  to the fix stage, whose answer is run again. The thinking window shows one ✓/✗ line per run,
  or a note when the sandbox is not installed. Each run is saved (`executions` table) and
  exported as a reward (`execution` in SFT rows; `chosen_execution` / `rejected_execution` in
  pairs).
- **Settings**: `TEMPO_SANDBOX` (auto/off), `TEMPO_SANDBOX_MATH` (auto/rules/off),
  `TEMPO_SANDBOX_TIMEOUT` (10 s), `TEMPO_SANDBOX_MEMORY_MB` (256), `TEMPO_SANDBOX_OUTPUT_KB` (64).
  `tempo-server sandbox status|run`, `doctor` and `setup` cover it.
- **Safety tests** (`tests/test_sandbox.py`, 31): endless loops (Python and JS), huge output,
  memory bombs, disk filling, deep recursion, reading `/etc/passwd`, `/`, `..`, `C:/Windows`, the
  host's real files, writing the standard library, sockets, DNS and `urllib`, `subprocess`,
  `os.system`, `fork`, `exec`, host environment variables, fresh runs, parallel runs, a bad
  checksum, an archive escaping its folder, a stale compiled cache. All stopped cleanly.
  Pipeline tests (`tests/test_sandbox_checks.py`, 22). CI installs the sandbox on every system
  (cached) with `TEMPO_SANDBOX_REQUIRED=1`, so these tests run there, never skip.
- **docs/SANDBOX.md**: the choice, what code can and can't do, how answers are checked, settings
  and limits. README, ARCHITECTURE (§5 checks, tech stack) and USE_CASES (#1, #3, #12) updated.
- Tests: 433 pass locally with the sandbox installed (Linux, Python 3.11), lint clean.

**CI results, step 8** (manual run 36 on `main`, commit `c91615d`, 2026-09-29; all green; the
sandbox installed on every system and its tests required, never skipped):

| System | Server tests incl. 31 sandbox safety + 22 sandbox pipeline tests | SDK job | Install tests |
|---|---|---|---|
| Linux | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓, Node 20 ✓ | ✓ |
| Windows | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓ | ✓ |
| macOS | 3.13 ✓ | Node 22 ✓ | ✓ |

The run before it (33) failed on Windows only: each sandbox run left its temporary folder
behind, because Wasmtime still held the folder and output files open when they were deleted.
Fixed in `c91615d` (close Wasmtime's objects first, then delete with short retries), found by
`test_each_run_is_fresh_and_leaves_nothing`.

### Step 9: training kit, and a first end-to-end tuning test

The whole path is in **[TRAINING.md](./TRAINING.md)**: collect, prepare, upload to Kaggle, run,
download, import, compare, promote.

- **Two Kaggle notebooks** in `training/` (generated from `tempo/notebooks.py` by
  `tempo-server train notebooks`; a test keeps the committed files in sync):
  - `tempo_core.ipynb`: Qwen3-1.7B (Apache-2.0), LoRA SFT on `export-sft` (loss on the answer
    only), DPO on `export-pairs` (same LoRA), merge, GGUF with llama.cpp (pinned: release b11249,
    binaries by SHA-256, converter by commit), Q4_K_M, Modelfile.
  - `tempo_router_judge.ipynb`: Laya (Apache-2.0) on `export-laya`, following Laya's official
    notebook (same preprocessing, loss, `torchrun` on both T4s, temperature calibration), with
    held-out accuracy per decision before and after.
  - Both: GPU check, progress line every minute (step, share, elapsed, time left, loss, session
    time left), checkpoints, a clean stop 20 minutes before the time budget (11 of Kaggle's 12
    hours), resume from an earlier version's output added as input, and `report.json` +
    `REPORT.md` (status, losses, held-out scores, data mix, licences of base, sources and answer
    models, files with SHA-256, times).
  - The code is the training kit `tempo/trainkit.py`, standalone; it travels in the pack as
    `tempo_trainkit.py`, so a notebook always runs the kit that matches its data.
- **`tempo-server train prepare`**: the three exports with one held-out split, licence checks
  on every training row (a source licence, no share-alike or non-commercial source, every
  writing or grading model "yes"; any problem stops it), the data mix (stops unless `--force`,
  which the pack records), size notes, `tempo-pack.json`, one zip, the notebooks next to it, and
  the exact upload steps. Export rows now carry `task_type`.
- **`tempo-server models import PATH`** (the downloaded zip, its folder or a `.gguf`): checksums
  from the report; Tempo-Core registered with Ollama over its HTTP API as `tempo-core:<version>`
  (chat template, stop word, Apache-2.0 licence so its outputs count as "yes"); Laya copied and
  test-loaded. **`tempo-server models compare`**: held-out questions on old and new (Ollama, or a
  GGUF on llama.cpp's server), graded by quick checks, a judge from another family and the
  sandbox; the gate per task type (30+ questions, no drop), then more wins than losses, a 95%
  bootstrap interval above zero, repetition within 5% and 8+ tokens/s on this CPU. Laya: per
  decision (50+ rows, no drop, one improves). **`tempo-server models promote`**: Tempo-Core
  versions answer only the task types they won (routes file read by the registry; the router
  skips them elsewhere, so versions serve side by side); Laya becomes `TEMPO_LAYA_MODEL`.
  `tempo-server models --free` still works (models is now a command group).
- **`tempo-server train dry-run`**: no keys, no GPU: 40 built-in demo questions (a new
  `tempo-demo` dataset, Apache-2.0, never offered by `collect`) answered by the demo models
  (now marked Apache-2.0: Tempo's own text), prepare, both notebooks executed cell by cell on
  the CPU (100 LoRA SFT steps and 20 DPO steps on a 40M-parameter random copy of Qwen3's
  architecture with Qwen3's tokenizer; a small random Laya), GGUF conversion, import, compare
  against the untuned tiny base, promote. About 4.5 minutes here. As expected, the tiny
  Tempo-Core writes nonsense and its gate keeps the old version; the tiny Laya improves and is
  promoted. Tested locally: the time-budget stop and resume (SFT stopped at step 14, the next
  run resumed from checkpoint-14 and finished).
- **Training libraries** only in the optional `tempo-server[train]` extra.
- **CI**: new Linux job `train-dry-run` on every push (CPU PyTorch, then `.[train]`; downloads
  cached), under 10 minutes.
- Not tested here: the notebooks on a real Kaggle GPU (the `torchrun` two-GPU path for Laya, fp16
  on a T4, real run times). The first real run measures them.
- Tests: 408 pass locally (Linux, Python 3.11; the 50 sandbox tests skip here because its runtimes are not installed in this session, CI requires them), lint clean.

**CI results, step 9** (manual run 43 on `main`, commit `a61ff5c`, 2026-09-29; all green; the
sandbox installed and required on every system):

| System | Server tests | SDK job | Install tests | Training dry run |
|---|---|---|---|---|
| Linux | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓, Node 20 ✓ | ✓ | ✓ (8 min: 1 min install, 6.6 min dry run) |
| Windows | 3.11 ✓ 3.12 ✓ 3.13 ✓ 3.14 ✓ | Node 22 ✓ | ✓ | Linux only (GGUF step) |
| macOS | 3.13 ✓ | Node 22 ✓ | ✓ | Linux only |

Lint and the package build pass. The push runs of each step-9 commit (Linux) were green too.

### Step 10: faster answers, never cut an answer, the laptop test's fixes

Full description and measurements: **[SPEED.md](./SPEED.md)**. The owner's laptop test (Groq,
NVIDIA and Google keys, 60 s budget) found: Best mode waiting for the slowest model;
nemotron-3-super taking 43 s of the 60; "using the best answer so far" followed by "No answer was
produced within the budget"; "No providers configured" while three providers answered; a
Gemini 2.5 model "not found"; a count of 99 unconfigured providers; an 846 MB Laya download it
hadn't asked for; and a temperature warning from Laya.

1. **Faster answers**
   - The first good answer is shown as soon as its own model finishes (`answer_ready`), even
     while parallel drafts are still writing. Checks go on in the background. The answer stays
     unless a check finds a real problem; then the fix replaces it and the page shows what
     changed (`answer_revised`, a line diff). CLI and web both show it.
   - Models are picked by **measured speed** (`tempo/speed.py`): a rolling record (last 20
     calls) of time to first token and tokens a second per model, loaded from the log at
     start-up. It is blended in seconds per token with the registry's figures (worth one call),
     so one slow call is enough. Calls stopped at the time limit count too. Fast mode weighs
     speed most; Auto weighs it by how hard the question is.
   - Warm-up at server start: Ollama loads the local model that would answer first; Laya runs
     its first predictions while loading and uses a checkpoint on disk with no network check.
   - Laya: `TEMPO_LAYA_BACKEND=auto` (new default) times PyTorch and ONNX Runtime fp32 once per
     checkpoint and machine and keeps the faster. It isn't asked when the rules are sure
     (logged as "sure"). `doctor` shows the runner, why, and the time per kind of decision.
   - `tempo-server bench`: seconds to the first token, to the answer being ready, and to every
     stage done, per mode.
2. **Know the limits; never cut an answer**
   - The expected answer length comes from the question. Stages are planned to fit the time
     budget from measured speed, counting reasoning and continuations (the `plan` event shows
     the estimates).
   - A stage starts only with a model that can finish in the time left ("can't finish in the
     time left"). In Best mode, a draft that would crowd out the check and merge is left out.
   - When the time is up no new stage starts. An answer already arriving is finished, up to
     `TEMPO_FINISH_GRACE` (120 s). Parallel drafts that can't change the shown answer are
     stopped.
   - With an answer in hand, it is returned with a plain note instead of an error.
   - `finish_reason` "length": the same model, or the next one, continues it (up to 3 times),
     and the thinking window says so.
3. **Header**: `GET /api/status` counts the models ready for the caller, including keys
   `tempo-server setup` stored in the vault (before, only environment keys counted), and only
   chat-capable models. The web header, empty chat and CLI use it.
4. **Models the provider no longer offers**: before routing, each usable provider's model list
   is read if it wasn't in the last 24 hours (once per run; the question waits at most 6 s, and
   a list that can't be read never stops the answer). Seeds it lacks are skipped with a note. "Model not found" marks a model as not offered, so no
   attempt is wasted on it again. Both are saved in `catalog.json`.
5. **No model yet**: one line everywhere ("No model yet: run `tempo-server setup` to add a free
   key, or start Ollama.") instead of the list of 99.
6. **Laya download**:
   - Nothing is downloaded unless the `laya` extra is installed. Without it, the exact install
     command is shown (on Linux without a GPU, CPU PyTorch first).
   - A first download says what and how big (about 846 MB) before it starts, and `doctor` has a
     Laya line.
   - The "invalid temperatures ... choice:11+=0.10 -> 0.5" warning is **Laya's**: the stock
     checkpoint ships a value below Laya's own 0.5 minimum, for a bucket Tempo never uses. Tempo
     now logs one plain line for that case.
   - Tempo's own fine-tunes had the same potential bug (fitted 0.1–10): they now fit within
     0.5–5.
   - Upstream issue drafted, **not posted**: [upstream/laya-choice-11-temperature.md](./upstream/laya-choice-11-temperature.md).
7. **Faster CI** (`.github/workflows/ci.yml`): the Playwright browser is cached (keyed on its
   version; only system libraries are installed on a hit), uv's and pip's downloads are cached
   in the install job, and every pip cache keys on `pyproject.toml`. A newer push cancels the
   stale run on branches and pull requests; runs on `main` are kept. **Not yet seen running in
   CI** (no runner, below).

Also fixed on the way:
- A history browser test failed in the first 4.8 hours after midnight (it set "yesterday" as
  now minus 1.2 days).
- The screenshot fixture no longer deletes other screen sizes.
- The test suite now fails any HTTP request to a host other than this computer. That caught a
  browser test reading Groq's real model list with a placeholder key.

**Speed before and after, fake providers** (`scripts/speed_compare.py`: the laptop's timings;
nemotron 6 s to the first token and 43 s in all, Groq 0.3/1.5 s, Gemini 2.5 "not found"; median
seconds over 9 questions per mode, on a virtual clock):

| | Complete answer on screen, before → after | Done, before → after | "Not found" attempts |
|---|---|---|---|
| Fast | 6.0 → **1.5** | 6.0 → **2.3** | 1 → **0** |
| Auto | 45.2 → **2.5** | 45.2 → **3.0** | 1 → **0** |
| Best | 60.0 → **1.5** | 60.0 → **18.5** | 2 → **0** |
| Best, 30 s budget | **no answer 9 of 9** → 1.5 (9 of 9 answered) | 30.0 → 18.5 | 2 → **0** |

No run went past its budget. The slowest first question in Fast and Auto is still 43 s: nemotron
looks fast on paper until it has been measured once.

**CI, step 10**: not run. Since 2026-09-29 16:47 UTC every job of every run (pushes and pull
request #2) ends within seconds without a runner (`runner_id` 0, no steps, no logs); see
"Blocked: needs the owner". Local results instead: 510 tests pass on Linux (Python 3.11;
the 50 sandbox tests skip here, as before), lint and format clean. The browser tests (28)
pass here too.

**Decisions for the owner (step 10)**

1. **CI has no runners** (blocked, below): check the Actions minutes and spending limit, and
   decide whether to merge pull request #2 before its CI can run. Decided 2026-09-30: the owner
   asked to put everything on `main`; it was merged after a full local run of the Linux CI jobs
   ("Merged to `main`" below step 11).
2. **The shown answer stays** unless a check fails it: a draft the judge scores equal or only
   slightly higher does not replace it, even in Best mode. (Merging parallel drafts still
   happens when the check fails.) Keep, or let a clearly higher score replace a passing answer?
3. **`TEMPO_FINISH_GRACE` 120 s**: how long an arriving answer may run past the budget. Lower
   it if the time budget should be closer to a hard wall.
4. **Laya `auto` backend**: compares the two fp32 runners only with 12 GB of memory or more
   (both loaded once). Lower the threshold, or always stay on PyTorch?
5. **Skip Laya when the rules are sure** (on by default): less shadow data for the cases the
   rules find easy. Turn off (`TEMPO_LAYA_SKIP_SURE=0`) while collecting Laya training data?
6. **Model lists** are re-read once a day per provider, before the first question of the day
   is routed (that question waits up to 6 s for them). Shorter or longer, or read them in the
   background at server start instead?
7. **Measured speed**: the registry's figures count as one measured call, and the last 20 calls
   are kept. A model that was slow once is avoided until measured again (by bench, or when
   nothing faster fits). Fine, or should an old slow call expire sooner?
8. **Post the Laya issue?** The draft is ready; nothing was posted.

### Step 11: the new web UI

Full description: **[WEB_UI.md](./WEB_UI.md)**. Screenshots (phone 390, tablet 820, desktop 1440; light
and dark; home, history drawer, live thinking timeline, answer, preview, maths, Models, Usage, Keys,
Developers, shortcuts, landing page, demo): [`docs/screenshots/`](./screenshots).

- **Look**: white-and-brown theme, dark brown theme (system by default; the button cycles
  system, light, dark), animations everywhere (messages, streaming caret, timeline, chips, hover
  and press, page transitions, skeletons) that stop under `prefers-reduced-motion`, phone/tablet/
  desktop layouts (sidebar drawer on small screens). Plain ES modules, no framework, no build
  step. Fonts (Inter, JetBrains Mono, Noto Sans Gujarati and Devanagari; OFL), KaTeX and Mermaid
  are bundled with their licences ([tempo/web/LICENSES.md](../tempo/web/LICENSES.md), NOTICE); the
  page loads nothing from the network (a test checks every request and every URL in the code).
- **Thinking timeline**: a node per step that lights up while it runs, model chips (spinner,
  tick or cross) as they are called, notes for checks, sandbox runs, fallbacks; full log below.
- **History**: `tempo/history.py`, `/api/chats*` (list with search, open, save, rename, pin,
  delete, export Markdown/JSON), per user, in the data folder, own table (never read by any
  training export; tested); sidebar groups Pinned/Today/Yesterday/Last 7 days/Older; an old chat
  reopens with its timeline and continues. "Delete my data" removes chats too. **Private** mode:
  local models only and `save: false`: nothing in history, question log, calls, stages,
  decisions or cache (tested).
- **Assistant**: streaming with Stop (button and `Esc`), regenerate, edit and resend, copy answer
  and code; own safe Markdown (tables, lists, quotes, task lists) with maths (KaTeX, only when
  needed) and syntax highlighting; HTML/SVG/Mermaid preview in a sandboxed frame with a CSP that
  blocks the network, download, expand; attach files and images (button, drag and drop, paste;
  images only when a vision model is ready); mode picker, model picker, stage/time/free-request
  settings; Models, Usage, Keys, Developers restyled; status badge; shortcuts + help panel;
  friendly errors with next steps (add key, see models, retry, enter API key).
- **Voice**: `/api/speech/transcribe` and `/api/speech/say` call Groq's free whisper and Orpheus
  (limits and 200-character input from console.groq.com, checked 2026-09-29); `/api/capabilities`
  hides the buttons without a key. Tested against a fake provider only (**needs key**).
- **Quality**: first load median 80 ms to `load`, 70 ms to first paint, 19 requests, 211 KB
  (five runs, localhost). axe-core audit clean on every page in both themes (scrollable regions
  made keyboard-focusable). Browser tests `tests/test_web_e2e.py` (Playwright): first load and
  no external requests, streaming and timeline, Markdown/maths/code, previews sandboxed and
  downloadable, Stop/`Esc`/regenerate/edit, history end to end, Private, attachments, errors,
  shortcuts, theme, reduced motion, pages, delete-my-data, voice, small screens, WCAG AA
  contrast in both themes, docs pages, screenshots. New CI job `web-e2e` (Linux; sets
  `TEMPO_REQUIRE_E2E=1`, keeps the screenshots as an artifact).
- **docs/index.html and docs/demo** restyled to the same theme with the app's screenshots; the
  demo replays recordings with the same timeline and still works from a file. `docs/assets/`
  holds copies of the theme and fonts (a test fails when they drift).
- Tests: 453 pass locally (Linux, Python 3.11; 50 sandbox tests skip here), lint clean.
  CI on pull request #1 was green on every system (Linux, Windows, macOS; server tests, SDKs,
  install, browser tests, training dry run) after one Windows fix (a cache rule for font paths
  with backslashes), and it was merged into `main` (`ec764cc`, 2026-09-29).

**Decisions for the owner (step 11)**

1. ~~**Branch**~~: settled; step 11 was merged into `main` through pull request #1.
2. **Mermaid ships EPL code**: Mermaid's single-file build bundles elkjs (EPL-2.0), unmodified.
   It adds 5.5 MB to the package (2.6 MB wheel in total) and loads only when you press Preview
   on a diagram. Keep it, or drop `vendor/mermaid/` and its branch in `js/preview.js`
   (details in tempo/web/LICENSES.md).
3. **Voice is Groq-only** and hidden without a key. The browser's own speech features are not
   used (Chrome's speech recognition sends audio to Google, against "only your computer").
   Read-aloud reads at most about 1,100 characters per click to protect Groq's free
   100-requests-a-day limit; the voice name (`hannah`) is one of three Groq's docs name.
4. **Private mode** also skips the answer cache and feedback (no question id is kept).
5. **PDF attachments** are refused with a clear message (no PDF reader bundled yet).
6. **Chat size**: a saved chat is limited to 12 MB (images are shrunk to 1280 px first).

### Pre-public check (2026-09-30)

The owner asked to make the repository public. A session can't change a repository's
visibility, so this is the check before the owner switches it (PUBLISHING.md §2 has the steps).

- **No keys or secrets** in any commit on any branch (`main`, `claude/friendly-dirac-uzfo1y`,
  `claude/great-cerf-73arjx`; 106 commits, full clone):
  - gitleaks 8.28.0 (`git --log-opts=--all`, 105 commits with changes, ~8.2 MB) and `dir` on the
    working tree: one finding, a false positive (`generic-api-key` on `g.keyCount` in the
    vendored `tempo/web/vendor/mermaid/mermaid.min.js`).
  - The provider key-format pass from 2026-09-29, over every added line in every commit, plus
    Anthropic, JWT and Kaggle formats: nothing. Non-empty `*_API_KEY` / `*_TOKEN` /
    `*_ACCOUNT_ID` assignments: only test values (`OPENAI_API_KEY=local`, a six-character test
    account id).
  - No `.env`, key file, database or credentials file was ever committed (only `.env.example`).
- **One personal email**: every commit is by `Claude <noreply@anthropic.com>` except the merge
  commit of pull request #1 (`ec764cc`, on `main`), made in GitHub's web page, whose author is
  the owner's personal email address. The 2026-09-29 line "the owner's email appears nowhere" was
  true before that merge. Decision for the owner below.
- **Screenshots** (`docs/screenshots/`) show demo mode only (providers "Alpha" and "Beta", no
  fingerprints). `tempo/data/*.yaml` are hand-written probe and example questions, not
  training rows. No training data is committed.
- **Workflows are safe for forks**: no `pull_request_target` or `workflow_run`, `contents: read`
  by default, publishing only on releases and manual runs. Once public, Windows and macOS CI
  also run on every push to `main` (step 6 decision; switches on by itself).
- Licence (Apache-2.0), NOTICE, `tempo/web/LICENSES.md`, CONTRIBUTING.md and SECURITY.md are in
  place.
- Tests and lint on `main` (`ec764cc`, fresh venv, Python 3.11): 423 passed, 51 skipped; ruff
  check and format clean.

**Decisions for the owner (pre-public)**

1. **The email in `ec764cc`**: accept it (it becomes public), or rewrite `main` so that commit
   uses the `noreply` address before switching. A rewrite changes every later commit id and
   needs a force push to `main`, so it wasn't done. Either way, turn on "Keep my email
   addresses private" (GitHub → Settings → Emails).
2. ~~**Branches**~~: settled; everything is on `main`, and the owner deletes the other branches
   ("Repo tidy and one workflow" below).

### Merged to `main` (2026-09-30)

The owner asked to keep everything on `main`, validate it, and delete the other branches.

- `main` was fast-forwarded to `claude/friendly-dirac-uzfo1y` (step 10, pull request #2: 17
  commits, already based on `main`), and the pre-public check commit went on top. No merge
  commit was made on GitHub's page, so no new commit carries a personal email.
- **Validation** (GitHub Actions starts no jobs, so the Linux CI jobs were run here, on the
  code that was pushed, `dc6a047`; the commit after it only changes this file; Python 3.11):
  - lint and format: clean;
  - full suite with the sandbox installed and required (`TEMPO_SANDBOX_REQUIRED=1`) and the
    browser tests required (`TEMPO_REQUIRE_E2E=1`): 566 passed, none skipped;
  - package: `python -m build` and `twine check --strict` pass;
  - SDKs against a demo-mode server: Python 6 passed (build and `twine check` pass), JS 6
    passed (`npm pack --dry-run` fine);
  - training dry run (`tempo-server train dry-run`, PyTorch CPU build): every stage passed in
    412 s (both notebooks, import, compare, promote; Laya promoted, the tiny Tempo-Core held
    back by the gate, as expected).
  - Not run here: the Windows and macOS jobs and the install scripts on those systems (step 10
    didn't change `install.sh`, `install.ps1`, the SDKs or the sandbox).
  - Playwright was pinned to 1.56.0 in the local venv only, to match this machine's Chromium
    (build 1194); the project's `playwright>=1.50` is unchanged.
- **Branches to delete** (owner): `claude/friendly-dirac-uzfo1y` and `claude/great-cerf-73arjx`
  are ancestors of `main`; every line of `claude/happy-wozniak-ibsb6c` is on `main` (its one
  commit was re-applied as `dc6a047`). GitHub shows pull request #2 as merged. The session's
  `git push --delete` was refused (HTTP 403, the environment's policy), so delete them at
  github.com/narendrachampaneri/Tempo-server/branches.

### Repo tidy and one workflow (2026-09-30)

The owner asked for `main` as the only branch, one clear workflow, and a check of the folders.

- **The extra commit on `claude/happy-wozniak-ibsb6c`** (`beab691`, "Pre-public check: history
  scan of every branch, owner steps to switch") is the first copy of `main`'s `dc6a047`: same
  message and time, made on the older `main` (`ec764cc`) before step 10 went in. Its
  PUBLISHING.md section is on `main` unchanged, and every line it adds to STATUS.md is on `main`
  except one sentence `main` has since replaced ("`claude/friendly-dirac-uzfo1y` has 17 unmerged
  commits", out of date once step 10 was merged). Nothing to bring in, so no pull request for it.
- **The other branches**: `claude/friendly-dirac-uzfo1y` (steps 10 and 11, pull requests #1
  and #2) ends at `ec76259` and `claude/great-cerf-73arjx` ends at step 9 (`9ef6cc1`, "STATUS:
  step 9 CI results per system"). Both are ancestors of `main`: no commit to lose. All three
  are safe to delete. The session's delete was refused again (HTTP 403), so the owner deletes
  them ("Blocked: needs the owner").
- **Workflow**: CLAUDE.md rules 11–14 (one step = one branch = one pull request into `main`;
  never push to `main`; merge only when CI is green on Linux, Windows and macOS, with a merge
  commit; then delete the branch). CONTRIBUTING.md says the same for contributors. The full CI on
  `main` is a manual run (Actions → CI → Run workflow), because a push to `main` runs the Linux
  jobs only while the repository is private.
- **Folders**: ARCHITECTURE.md §10 described only a proposed layout (`tempo/api/`, `brain/`,
  `cli/`, a Next.js `web/`, `evals/`). The code is still the flat `tempo/` package that section
  names for Phases 1 and 2, so nothing moves. §10.1 is now a folder map of what is on `main`, the
  proposed tree is §10.2 with today's nearest equivalents, and §9 says what was built instead of
  Redis, Postgres and Next.js (SQLite; plain HTML, CSS and JavaScript).
- **Removed**: `docs/PR_STEPS_1-6.md`, the pull request text for steps 1–6 from a branch that is
  gone (`claude/multi-model-ai-platform-o0fx9v`). Nothing linked to it, and every point in it is
  in this file in more detail. Checked and kept: `docs/assets/` (the GitHub Pages copy of the web
  app's theme and fonts; a test keeps them identical), the LICENSE copies in `sdk/python/` and
  `sdk/js/` (each package ships its own), `docs/upstream/` (a draft issue the owner hasn't
  decided on) and `docs/examples/collect-workflow.yml` (a workflow to copy, deliberately
  inactive). No untracked or scratch files are in the repository.
- **Merged without CI** (text files only, as the owner allowed for today): CLAUDE.md,
  CONTRIBUTING.md, ARCHITECTURE.md, this file, and the removed PR text. Tests and lint pass
  locally. Code changes wait for CI.

## Live catalog on 2026-09-28 (public data, no keys)

| Provider | Listed | Chat-capable | Other types | Health |
|---|---|---|---|---|
| OpenRouter (free) | 18 | 17 (16 chat, 1 code) | 1 safety | 14 ok, 2 degraded (qwen3.8-27b at 92%, nemotron-3-nano-omni at 78%), 1 last fallback (`openrouter/free`) |
| NVIDIA (trial, off by default) | 80 | 65 (47 chat, 10 vision, 8 code) | 8 embedding, 5 safety, 2 decision | listed (NVIDIA publishes no per-model health) |
| OpenCode Zen (free, own key) | 10 | 9 | 1 decision (Jev) | listed |
| Groq, Google, Cloudflare, Cohere, Mistral | needs key | seeds only: Groq 3 chat + 7 others, Google 2 | | not checked |

Previews: Big Pickle, LongCat preview, Space Bunny (Zen), dots-3-note-preview, stealth
space-bunny-alpha (OpenRouter). Specialists: Palmyra Fin/Med (NVIDIA), Ling Fin (Zen), Ling
Sante (OpenRouter, health).

## Terms verdicts (may outputs be training data?)

| Provider | Verdict | Data policy (free tier) | Tempo's use |
|---|---|---|---|
| Ollama (local) | per model licence (Apache-2.0 / MIT: yes) | ok | default for private and training runs |
| Mistral | **yes** (text outputs) | may-train until opted out (`TEMPO_OPTED_OUT`), then may-log | answering; terms re-read before exports |
| Cloudflare | per model licence (Cloudflare adds no limit) | ok (does not train or keep) | answering |
| Groq | unclear (until confirmed in writing) | unknown | answering |
| OpenRouter | unclear (until confirmed in writing) | may-log (all free models) | answering; `openrouter/free` last |
| Cerebras | unclear | unknown | off; never eval or collect |
| Google AI Studio | no | may-train | answering |
| NVIDIA | no | may-train | off; owner's private testing only |
| Cohere | no | may-train | users' own keys; answers only |
| OpenCode Zen | no | per model | off; users' own keys; answering only |

## In progress

Nothing. Everything is on `main`; the full CI run on `main` waits for 1 October ("Next").

## Blocked: needs the owner

- **Delete the branches other than `main`** (the owner's request, 2026-09-30); the session's
  delete is refused (HTTP 403). None holds anything that isn't on `main` ("Repo tidy and one
  workflow"): `claude/friendly-dirac-uzfo1y`, `claude/great-cerf-73arjx`,
  `claude/happy-wozniak-ibsb6c`, and `claude/zealous-heisenberg-g1cf37` once its pull request is
  merged. Delete them at github.com/narendrachampaneri/Tempo-server/branches. To make this
  automatic: Settings → General → "Automatically delete head branches".
- **Going public** (the owner's request, 2026-09-30): the check is done ("Pre-public check"
  above). The owner decides about the email in `ec764cc`, turns on secret scanning, push
  protection and private vulnerability reporting, then switches the repository to public
  (PUBLISHING.md §2).
- **GitHub Actions starts no jobs** (since 2026-09-29 16:47 UTC, still on 2026-09-30 01:19
  UTC). Every job ends in 3–10 seconds with no runner assigned (`runner_id` 0, no steps, no
  logs), on Linux, Windows and macOS, for pushes and pull requests, including runs of commits
  that only changed Python. So it is not the code or the workflow file.
  - The usual cause is the account's included Actions minutes or its spending limit being used
    up. This repository ran about 70 CI runs in two days, and macOS minutes count 10×, Windows
    2×. Check github.com → Settings → Billing and plans (Actions usage, spending limit), and the
    repository's Settings → Actions.
  - Pull request #2 was merged on 2026-09-30 at the owner's request after a local run of the
    Linux CI jobs ("Merged to `main`"); the first CI run on `main` is still owed.
  - Making the repository public should end this: "GitHub Actions usage is free for
    self-hosted runners and for public repositories that use standard GitHub-hosted runners"
    (docs.github.com/en/billing/concepts/product-billing/github-actions, checked 2026-09-30).
    Private repositories on GitHub Free get 2,000 minutes a month.
  - Ways to use fewer minutes, the owner's choice: macOS and Windows jobs only on `main` and
    pull requests (not every branch push), or the training dry run only on pull requests
    (decision 5 of step 9).

- **Publishing**: PyPI (Trusted Publishing to configure on pypi.org), the Docker image (a GitHub
  release), GitHub Pages (Settings → Pages) and the two SDK packages (PyPI and npm) wait for the
  owner. The one-line installers and
  the GitHub-archive install need the repository to be public.

- **The first real training run** needs the owner: real "yes" data (collect with Ollama on
  their computer) and a free, phone-verified Kaggle account. Steps: "Your first real training
  run" below.

## Blocked: needs key

No provider keys exist yet in this environment. Built and tested with mocks or a local fake
server:

| Item | Needs | Tested so far with |
|---|---|---|
| Real answers from Groq, Google, OpenRouter | `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | mock models; fake OpenAI-style server |
| Cloudflare Workers AI list and calls | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | mocked `/ai/models/search`; OpenAI-compatible route on a fake server |
| Cohere list, monthly counting, calls | a user's own trial key (`tempo-server keys add cohere --user NAME`) | mocked `/v1/models`, quota tests |
| Mistral list and header limits | `MISTRAL_API_KEY` | mocked `/v1/models`, header tests |
| NVIDIA calls (list is live already) | `NVIDIA_API_KEY` + `TEMPO_ENABLE_PROVIDERS=nvidia`, owner only | mocks |
| OpenCode Zen calls (list is live already) | `TEMPO_ENABLE_PROVIDERS=opencode` + a user's own key | mocks |
| `tempo-server export-sft` / `export-pairs` with real data | "yes" answers in the log: Ollama with an Apache-2.0/MIT model, or Mistral | the scripted test engine |
| OpenRouter live free daily limit (`/api/v1/key`) | `OPENROUTER_API_KEY` | mock |
| Live limits from `x-ratelimit-limit-*` headers | any key | header tests |
| `tempo-server eval` on real models | any key (never Cerebras or Cohere) | mock models |
| `tempo-server collect` with hosted models | a key (outputs stay "unclear", not exported) | mocks; local-only yes run tested with mocks |
| Local "yes" collection for real | Ollama with an Apache-2.0/MIT model | mocked `/api/tags` and `/api/show` |
| Native tool calls on real providers (Groq, OpenRouter, Mistral, Cloudflare) | keys | LiteLLM against a fake server; emulation works on any model |
| Real vision models (catalog `inputs`, OpenRouter live; Groq/Google seeds) | keys | demo and scripted models |
| Strict JSON reliability per real model | keys | demo and scripted models |
| `tempo-server setup` key checks against real providers | a key per provider | `verify_key` monkeypatched; its HTTP checks tested with mocks earlier |
| `tempo-server quota` with real limits (headers, OpenRouter `/key`) | keys | seed limits and recorded calls |
| Web voice input and read-aloud (Groq whisper and Orpheus, terms acceptance for Orpheus) | `GROQ_API_KEY` or a user's own Groq key | fake provider (`tests/test_web_history.py`, `tests/test_web_e2e.py`) |
| Web attachments of images to a real vision model | a key for a vision model | scripted models |
| Local first and the used-up fallback with a real Ollama | Ollama running | scripted local model |
| Demo re-recorded with real models | keys | recorded in demo mode |
| `models compare` judged by real models | any key, or a local judge model | the demo judge (dry run) |
| `tempo-server bench` with real providers (real speed, not fakes) | keys | fast, slow and failing fake providers; `scripts/speed_compare.py` |
| Model-list check before routing, and "model not found" marking, on real Groq/Google lists | `GROQ_API_KEY`, `GEMINI_API_KEY` | fake lists (`tests/test_model_lists.py`) |
| Continuing an answer cut at a real model's output limit | keys | scripted `finish_reason` "length" |
| Header and `/api/status` with vault keys on a real server | keys stored by `tempo-server setup` | a fake vault key |
| Ollama warm-up at server start | Ollama running | mocked `/api/generate` |
| Laya `auto` runner choice on a real machine | the `laya` extra and 12 GB+ memory | fake runners (`tests/test_laya_speed.py`) |

Also blocked, on the owner rather than keys: publishing the Docker image, the PyPI package and
the GitHub Pages demo (the owner said not to publish yet).

## What to run once keys exist

Keys go into this environment's API credentials (the proxy adds them); use placeholder values
in files and never print a key.

```bash
. .venv/bin/activate
tempo-server sync                         # every configured list: listed / new / no longer listed / key ok
tempo-server models --free                # the live catalog, now with Groq, Google, Cloudflare, Cohere, Mistral
tempo-server ask "hi there!"              # then the other step-1 questions, without TEMPO_ENABLE_MOCK
tempo-server ask "A train travels 240 km in 3 hours, then 180 km in 2 hours. What is its average speed in km/h?"
tempo-server ask "ગુજરાતની રાજધાની કઈ છે?"
tempo-server ask --provider cloudflare "Write a haiku about rain"   # a few real calls per new provider
tempo-server ask --provider mistral "Summarise the water cycle in two sentences"
tempo-server keys add cohere --user NAME  # a user's own trial key; Cohere then answers for that user
tempo-server eval --task math --limit 2   # never uses Cerebras, Cohere or Zen (blocked in code)
tempo-server terms --check                # re-verify every quote
# With Ollama and an Apache-2.0 model (qwen3:8b, granite3.3:8b):
tempo-server collect --estimate --yes-only && tempo-server collect --yes-only --limit 10
tempo-server export-sft --out sft && tempo-server export-pairs --out pairs && tempo-server export-laya --out laya
tempo-server serve & python examples/tools_and_json.py   # tools, strict JSON and an image on real models
tempo-server quota                        # real limits after a few calls
tempo-server bench                        # real speed per mode: first token, answer ready, done
tempo-server bench --modes best --time-budget 30   # the laptop case: an answer within 30 s
tempo-server doctor                       # the Laya line: runner, why, and time per decision
tempo-server record-demo                  # re-record the public demo with real models, then commit docs/demo/recording.js
```

On the owner's own computer (not this environment), `tempo-server setup` is the way to add
keys: they are checked and stored encrypted, only for the owner.

Check that a real 429 cools the model down and falls back, that `x-ratelimit-limit-*` headers
update the limits (`tempo-server models --free` shows them), and that Mistral's headers are parsed
(their exact names were not documented on a reachable page).

## Your first real training run (owner)

1. Collect on your computer with a local Apache-2.0 model until `tempo-server train prepare`
   shows about 2,000 SFT rows, 1,000 pairs and 3,000 Laya decisions with no errors (days, in the
   background): `ollama pull qwen3:8b`, then `tempo-server collect --yes-only`.
2. `tempo-server train prepare` → upload the zip as a private Kaggle dataset.
3. Import `training/tempo_core.ipynb` and `training/tempo_router_judge.ipynb` into Kaggle; GPU
   T4 x2, Internet on, add the dataset; Save & Run All.
4. Download each output, then `tempo-server models import <zip>` (Ollama running),
   `ollama pull qwen3:1.7b` (the first comparison's "old"), `tempo-server models compare`,
   `tempo-server models promote`.
5. Send back both `REPORT.md` files and the compare output: they replace the time estimates.

## Next

- **1 October (the Actions minutes reset)**: the full CI on `main` (Actions → CI → Run workflow
  on `main`: every job on Linux, Windows and macOS). Step 10 has never run on GitHub (it was
  checked locally, on Linux only; "Merged to `main`"). Fix anything that fails, each fix in its
  own branch and pull request, merged once its CI is green on all three systems.
- Step 10 follow-ups (noted, not started): switch the shown stream to a parallel draft that
  finishes first when the streaming model is slow (today the first model to send a token keeps
  the stream); per-provider rate-limit waits in the time estimates; `bench` results in the
  Usage page.

- The owner's plan: `tempo-server collect --yes-only` on their computer with a local Apache-2.0
  model, then the first exports.
- Step 9 follow-ups (noted, not started): GRPO with sandbox rewards (TEMPO_MODELS.md §2 stage
  3); the probe set (`evalset.yaml`) in `models compare`; a judge that sees both answers in
  random order; Tempo-Core on both T4s; `models promote --rollback`; Granite 3.3 2B as the second
  family.
- Later tasks (noted, not started):
  - A slimmer Docker image (owner's decision: 627 MB is fine for 0.1).
  - Drop the `tempo` alias before 1.0.
  - Google AI Studio's free limits in `models.yaml` cite a community list
    (github.com/raullenchai/free-llm-api-resources); re-check them on Google's own rate-limit
    page, which needs a signed-in console.
  - NVIDIA's model list gives no context sizes; routing assumes 8K.
  - Human reference answers for the data mix.
  - GitHub Actions warns that `actions/download-artifact@v5` runs on a deprecated Node 20; move
    to the next major version when it is out.
  - MCP: tools with real models (needs keys), progress notifications during long `ask` runs, and
    an A2A agent card (ARCHITECTURE Phase 4).
  - The JS SDK is built with TypeScript 5.9; TypeScript 7 (the native compiler) is now `latest`
    on npm. Move when its declaration output is proven identical.
  - Sandbox: tests Tempo writes itself when a code question has none; mutation testing for
    test-writing answers; units, fractions in words and several results for maths; keeping one
    warm interpreter to save the ~0.1–0.8 s start per Python run; the Docker image could ship
    the runtimes pre-installed.

## Decisions for the owner

Step 10: see the list at the end of the step 10 section above (CI runners first).

Step 9:

1. **Laya is promoted all or nothing** (one checkpoint serves every decision): only if no
   decision with 50+ held-out rows drops and one improves. Keep that, or load two checkpoints
   side by side (about 0.8 GB more memory each)?
2. **First comparison's "old" Tempo-Core** is the untuned base (`qwen3:1.7b` from Ollama). Also
   compare against the free APIs it would replace (costs judge and answer requests)?
3. **Speed gate** 8 tokens/s on the computer that runs `compare` (TEMPO_MODELS.md said about 10
   for 1.7B). Which number, and should it be measured on 4 threads exactly?
4. **Tempo-Core on one T4** in full precision with LoRA (not QLoRA as planned): simpler, no
   bitsandbytes. Keep, or add QLoRA and both GPUs for the 4B option later?
5. **The dry run in CI on every push** (about 6–8 minutes of Linux minutes each). Keep, or only
   on pull requests and manual runs?

Step 8 (still open):

1. **How the runtimes are installed.** `pip install tempo-server` brings Wasmtime; the Python and
   JavaScript interpreters (about 16 MB) are downloaded once by `tempo-server sandbox install` (or
   `setup`) from their GitHub releases, pinned by SHA-256. Keep that, or publish a small
   `tempo-server-sandbox` package on PyPI that bundles them (so everything comes from pip, at the
   cost of a ~15 MB package to publish and update)?
2. **Install the sandbox automatically** the first time a code or maths question needs it (a
   one-time 16 MB download during that question), or keep it an explicit step (today: setup offers
   it; the thinking window says when it is missing)?
3. **Maths by a model-written program** costs one extra free request per maths question that the
   rules can't handle. Keep `auto`, or default to `rules` (no extra request)?
4. **Model-program mismatches**: today they lower the score strongly but don't fail the check on
   their own (the program could be the wrong one). Make them a hard failure like rule-based ones?
5. **Wasmtime updates** every month; the dependency allows `>=49,<60`. Keep a wide range (security
   fixes arrive without a Tempo release), or pin one major version and update on purpose?

## How the checks were run (for the next session)

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,terms]"   # see CONTRIBUTING.md
pytest -q && ruff check . && ruff format --check .
export TEMPO_ENABLE_MOCK=1 TEMPO_DATA_DIR=memory TEMPO_SYNC_INTERVAL=0
tempo-server ask "hi there!"                    # and the other questions above
tempo-server serve --port 8766                  # web page; drive it with Playwright
                                         # (executable_path=/opt/pw-browsers/chromium)
env -u TEMPO_ENABLE_MOCK TEMPO_DATA_DIR=/tmp/tempo-data tempo-server models --free   # live, public
tempo-server terms --check                      # every terms page, live
```
