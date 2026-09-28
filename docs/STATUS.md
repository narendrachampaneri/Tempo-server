# Status

_Last updated 2026-09-28, at the end of step 5 (making Tempo-server easy for other people). Branch:
`claude/multi-model-ai-platform-o0fx9v`._

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
  for `tempo eval` and `tempo collect` (`blocked_for: [eval, collect]`).
- **Training data "yes" sources**: local Apache-2.0 or MIT models through Ollama (Tempo reads
  each installed model's licence from `/api/show`), public datasets with open licences
  (questions only), and the owner's own 👍/👎. Groq and OpenRouter stay "unclear". Every logged
  call records the model's licence and verdict; every exported row has `factors.source`
  (dataset and licence, or `tempo-traffic`) and `factors.output_terms` (verdict and licence per
  model). `tempo collect --yes-only` uses only "yes" models for every stage, judge included;
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
   `tempo terms --check` on 2026-09-28):
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
4. **`tempo models --free [--json] [--all]`**: live table of provider, model, type, context, max
   output, inputs, tools, limits, data policy, health, last check and status. It reads every
   list first and saves the catalog to `catalog.json` in the data directory, which one-shot CLI
   runs load.
5. **Data policy** per provider and model (`ok`, `may-log`, `may-train`), with the sentence and
   link. Privacy `no_logging` (API) or `--no-logging` (CLI) never reaches flagged models:
   Google's free tier, NVIDIA's trial, Cohere, Mistral's free plan, stealth models and Zen's
   Big Pickle, MiMo, Ling Fin, Nemotron and Space Bunny.
6. **Ollama licences** recorded per model; Apache-2.0 and MIT count as "yes";
   `tempo collect --yes-only`.
7. **[docs/COLLECT_ANYWHERE.md](./COLLECT_ANYWHERE.md)**: exact Windows steps (local
   Apache-2.0 models from two families, Task Scheduler) and a scheduled GitHub Actions job
   ([docs/examples/collect-workflow.yml](./examples/collect-workflow.yml), not active) that
   resumes across runs through the cached data directory. `tempo collect --minutes N` added.
- Also fixed: a question whose answers all failed ended with "answer passed its check"; it now
  ends with `not_passed` ("no answer passed its check; sent the best one").
- Also fixed (rule 4): stored keys were shown by their last four characters in `tempo keys
  list`, `tempo keys add`, the API and the Keys page. They now show a one-way fingerprint
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
- **Terms before every export**: `tempo export-laya`, `export-sft` and `export-pairs` first
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
- **`tempo export-sft`** (conversation → the final answer that passed its check) and
  **`tempo export-pairs`** (chosen = that answer, rejected = an earlier answer that failed,
  hard failures first), in TRL's formats. Only "yes" rows, the licence and source on every row,
  no 👎, only the owner's and `tempo collect`'s questions by default (`--user` adds a consenting
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
  (`tempo users consent NAME --on/--off`, `GET`/`PUT /api/consent`). Consenting users'
  questions join every export (Laya, SFT, pairs); text from Tempo's own traffic is scrubbed
  first (emails, phone numbers including Indian mobiles, card, Aadhaar and PAN numbers, IP
  addresses). Users delete their data with `tempo users forget NAME` or `DELETE /api/data`.
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
- **oasst2** in `tempo collect`: first user turns only, deleted/flagged/synthetic/toxic
  messages skipped, scrubbed, Apache-2.0 on every row (57,904 of 64,592 prompts kept).
- **Error codes**: 503 with `Retry-After` (a minute, or the next daily reset) only when no model
  is available or every free quota is used up; 502 with the reason when models answered but
  none gave valid JSON or a valid tool call.
- **Scrubbing**: US Social Security numbers (`[ssn]`) and IBANs (`[iban]`, mod-97 checked).

### Step 5: easy for other people

- **`tempo setup`** (`tempo/setup.py`): walks through Ollama, Groq, Google, OpenRouter,
  Cloudflare (asks the account id), Mistral, Cohere, OpenCode Zen, NVIDIA and Cerebras: where to
  get the key, free limits with source and date, training verdict and data policy. Keys are
  checked with the provider (`verify_key`), stored encrypted for the owner only, shown as a
  fingerprint. Rules applied: Cerebras stays off (no key asked); NVIDIA only after confirming
  "own private testing"; OpenCode Zen opt-in with the user's own key; Cohere only the user's own
  key. Non-secret settings (`CLOUDFLARE_ACCOUNT_ID`, `OLLAMA_API_BASE`,
  `TEMPO_ENABLE_PROVIDERS`) go to `<data dir>/settings.env`, which refuses secret-looking names.
  Then the live lists are read and it prints free requests a day per provider and the total.
  The admin (`TEMPO_API_KEY`) also gets the owner's setup keys; other users never do.
- **Quota view**: `tempo quota [--json]`, `GET /api/quota`, and a strip on the Ask page with
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
- **Public demo**: `tempo record-demo` saves the engine's events for a few questions to
  `docs/demo/recording.js`; `docs/demo/index.html` replays them (thinking window, stage dots,
  streamed answer, final meta; speed, skip, dark mode, phone width). Recorded in demo mode and
  labelled so on the page; works from `file://` and GitHub Pages.
- **README quick start**: install, `tempo-server setup`, `tempo-server serve`, connect an app;
  Docker; the name clash.
- Tests: 344 pass, lint clean.

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

Nothing. Step 5 (the last planned step) is finished; the owner decides what comes next.

## Blocked: needs key

No provider keys exist yet in this environment. Built and tested with mocks or a local fake
server:

| Item | Needs | Tested so far with |
|---|---|---|
| Real answers from Groq, Google, OpenRouter | `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | mock models; fake OpenAI-style server |
| Cloudflare Workers AI list and calls | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | mocked `/ai/models/search`; OpenAI-compatible route on a fake server |
| Cohere list, monthly counting, calls | a user's own trial key (`tempo keys add cohere --user NAME`) | mocked `/v1/models`, quota tests |
| Mistral list and header limits | `MISTRAL_API_KEY` | mocked `/v1/models`, header tests |
| NVIDIA calls (list is live already) | `NVIDIA_API_KEY` + `TEMPO_ENABLE_PROVIDERS=nvidia`, owner only | mocks |
| OpenCode Zen calls (list is live already) | `TEMPO_ENABLE_PROVIDERS=opencode` + a user's own key | mocks |
| `tempo export-sft` / `export-pairs` with real data | "yes" answers in the log: Ollama with an Apache-2.0/MIT model, or Mistral | the scripted test engine |
| OpenRouter live free daily limit (`/api/v1/key`) | `OPENROUTER_API_KEY` | mock |
| Live limits from `x-ratelimit-limit-*` headers | any key | header tests |
| `tempo eval` on real models | any key (never Cerebras or Cohere) | mock models |
| `tempo collect` with hosted models | a key (outputs stay "unclear", not exported) | mocks; local-only yes run tested with mocks |
| Local "yes" collection for real | Ollama with an Apache-2.0/MIT model | mocked `/api/tags` and `/api/show` |
| Native tool calls on real providers (Groq, OpenRouter, Mistral, Cloudflare) | keys | LiteLLM against a fake server; emulation works on any model |
| Real vision models (catalog `inputs`, OpenRouter live; Groq/Google seeds) | keys | demo and scripted models |
| Strict JSON reliability per real model | keys | demo and scripted models |
| `tempo setup` key checks against real providers | a key per provider | `verify_key` monkeypatched; its HTTP checks tested with mocks earlier |
| `tempo quota` with real limits (headers, OpenRouter `/key`) | keys | seed limits and recorded calls |
| Local first and the used-up fallback with a real Ollama | Ollama running | scripted local model |
| Demo re-recorded with real models | keys | recorded in demo mode |

Also blocked, on the owner rather than keys: publishing the Docker image, the PyPI package and
the GitHub Pages demo (the owner said not to publish yet).

## What to run once keys exist

Keys go into this environment's API credentials (the proxy adds them); use placeholder values
in files and never print a key.

```bash
. .venv/bin/activate
tempo sync                         # every configured list: listed / new / no longer listed / key ok
tempo models --free                # the live catalog, now with Groq, Google, Cloudflare, Cohere, Mistral
tempo ask "hi there!"              # then the other step-1 questions, without TEMPO_ENABLE_MOCK
tempo ask "A train travels 240 km in 3 hours, then 180 km in 2 hours. What is its average speed in km/h?"
tempo ask "ગુજરાતની રાજધાની કઈ છે?"
tempo ask --provider cloudflare "Write a haiku about rain"   # a few real calls per new provider
tempo ask --provider mistral "Summarise the water cycle in two sentences"
tempo keys add cohere --user NAME  # a user's own trial key; Cohere then answers for that user
tempo eval --task math --limit 2   # never uses Cerebras, Cohere or Zen (blocked in code)
tempo terms --check                # re-verify every quote
# With Ollama and an Apache-2.0 model (qwen3:8b, granite3.3:8b):
tempo collect --estimate --yes-only && tempo collect --yes-only --limit 10
tempo export-sft --out sft && tempo export-pairs --out pairs && tempo export-laya --out laya
tempo serve & python examples/tools_and_json.py   # tools, strict JSON and an image on real models
tempo quota                        # real limits after a few calls
tempo record-demo                  # re-record the public demo with real models, then commit docs/demo/recording.js
```

On the owner's own computer (not this environment), `tempo-server setup` is the way to add
keys: they are checked and stored encrypted, only for the owner.

Check that a real 429 cools the model down and falls back, that `x-ratelimit-limit-*` headers
update the limits (`tempo models --free` shows them), and that Mistral's headers are parsed
(their exact names were not documented on a reachable page).

## Next

- The owner's plan: `tempo collect --yes-only` on their computer with a local Apache-2.0 model,
  then the first exports.
- Noticed, not started:
  - Keys in `.env` or the environment (server-wide) are used for every user of a shared server
    when the user has no key of their own (decision 1 below); keys from `tempo setup` are
    already owner-only.
  - Google AI Studio's free limits in `models.yaml` cite a community list
    (github.com/raullenchai/free-llm-api-resources); re-check them on Google's own rate-limit
    page, which needs a signed-in console.
  - NVIDIA's model list gives no context sizes; routing assumes 8K.
  - Human reference answers for the data mix.
  - A PyPI publish workflow (trusted publishing), if the owner wants PyPI.

## Decisions for the owner

1. **Server-wide keys on a shared server**: keys from `tempo setup` are only for the owner, but
   keys in `.env`/the environment are also used for other users who have no key of their own.
   Make env keys owner-only too, with an opt-in `TEMPO_SHARE_SERVER_KEYS=1`? (Recommended, since
   "my keys are only for my own use"; it changes behaviour for existing shared servers.)
2. **Command name**: keep `tempo-server` as the documented command and `tempo` as an alias
   (today), or drop the `tempo` alias before 1.0 to avoid clashing with Grafana Tempo on PATH?
3. **Reserve the names**: publish a first `tempo-server` release to PyPI and create the
   `tempo-server` Hugging Face org soon, before someone else takes them?
4. **Docker image size** (627 MB, mostly LiteLLM and its dependencies): fine, or look into a
   slimmer image?
5. **Local first**: default `auto` with complexity up to 0.3 goes to a running Ollama model.
   Keep, lower, or make it opt-in?
6. **Demo hosting**: GitHub Pages from `/docs` on `main` (demo at
   `https://<owner>.github.io/Tempo-server/demo/`), or a separate `gh-pages` branch?

## How the checks were run (for the next session)

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,terms]"
pytest -q && ruff check . && ruff format --check .
export TEMPO_ENABLE_MOCK=1 TEMPO_DATA_DIR=memory TEMPO_SYNC_INTERVAL=0
tempo ask "hi there!"                    # and the other questions above
tempo serve --port 8766                  # web page; drive it with Playwright
                                         # (executable_path=/opt/pw-browsers/chromium)
env -u TEMPO_ENABLE_MOCK TEMPO_DATA_DIR=/tmp/tempo-data tempo models --free   # live, public
tempo terms --check                      # every terms page, live
```
