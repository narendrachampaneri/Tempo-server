# Status

_Last updated 2026-09-28, at the end of step 2 (live free-model catalog and new providers).
Branch: `claude/multi-model-ai-platform-o0fx9v`._

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

| Provider | Verdict | Data policy (free tier) | Notes |
|---|---|---|---|
| Groq | unclear | unknown | until Groq confirms in writing |
| OpenRouter | unclear | unknown (stealth: may-log) | until OpenRouter confirms in writing |
| Cerebras | unclear | unknown | off; never eval or collect |
| Google AI Studio | no | may-train | "develop models that compete" |
| Cloudflare | unclear | ok (does not train or keep) | outputs are yours; each model's licence applies |
| NVIDIA | no | may-train | trial: testing and evaluation only; off by default |
| Cohere | no | may-train | no competing product; no benchmarking (blocked for eval) |
| Mistral | unclear | may-train (opt-out) | only image outputs are restricted |
| OpenCode Zen | no | per model | "develop artificial intelligence models that compete" |
| Ollama (local) | per model licence | ok | Apache-2.0 / MIT: yes |

## In progress

Nothing. Step 2 is finished; waiting for step 3 from the owner.

## Blocked: needs key

No provider keys exist yet in this environment. Built and tested with mocks or a local fake
server:

| Item | Needs | Tested so far with |
|---|---|---|
| Real answers from Groq, Google, OpenRouter | `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | mock models; fake OpenAI-style server |
| Cloudflare Workers AI list and calls | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | mocked `/ai/models/search`; OpenAI-compatible route on a fake server |
| Cohere list, monthly counting, calls | `COHERE_API_KEY` | mocked `/v1/models`, quota tests |
| Mistral list and header limits | `MISTRAL_API_KEY` | mocked `/v1/models`, header tests |
| NVIDIA calls (list is live already) | `NVIDIA_API_KEY` + `TEMPO_ENABLE_PROVIDERS=nvidia` | mocks |
| OpenCode Zen calls (list is live already) | a user's own key (`tempo keys add opencode --user NAME`) | mocks |
| OpenRouter live free daily limit (`/api/v1/key`) | `OPENROUTER_API_KEY` | mock |
| Live limits from `x-ratelimit-limit-*` headers | any key | header tests |
| `tempo eval` on real models | any key (never Cerebras or Cohere) | mock models |
| `tempo collect` with hosted models | a key (outputs stay "unclear", not exported) | mocks; local-only yes run tested with mocks |
| Local "yes" collection for real | Ollama with an Apache-2.0/MIT model | mocked `/api/tags` and `/api/show` |

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
tempo ask --provider cohere "hello"   # counts against 1,000 calls a month
tempo eval --task math --limit 2   # never uses Cerebras or Cohere (blocked in code)
tempo terms --check                # re-verify every quote
# With Ollama and an Apache-2.0 model (qwen3:8b, granite3.3:8b):
tempo collect --estimate --yes-only && tempo collect --yes-only --limit 10
```

Check that a real 429 cools the model down and falls back, that `x-ratelimit-limit-*` headers
update the limits (`tempo models --free` shows them), and that Mistral's headers are parsed
(their exact names were not documented on a reachable page).

## Next

- Step 3, when the owner sends it.
- Noticed, not started:
  - NVIDIA's model list gives no context sizes; the catalog shows "?" and routing assumes 8K.
  - The web page has no switch for privacy `no_logging` yet (API and CLI do).
  - Language check: Latin-script languages other than the seven above are not told apart.

## Decisions for the owner

1. **NVIDIA** is off by default because its trial terms say "internal testing and evaluation
   purposes, not in production". Keep it off, or turn it on for your own testing?
2. **Cohere** trial keys are "evaluation keys", and Cohere's terms forbid benchmarking, so it is
   blocked for `tempo eval` and `tempo collect`. Should Tempo serve users with it at all?
3. **Mistral**'s terms restrict only image outputs, so its text outputs could count as "yes".
   It stays "unclear" under your rule until you decide. Its free plan also trains on prompts
   unless you opt out in the console (recommended).
4. **Cloudflare** says outputs are yours and each model's licence applies. Should open-licence
   models on Cloudflare (Apache-2.0/MIT) count as "yes", like local ones?
5. **Space Bunny** (Zen) is flagged "may-log" because it is a stealth model (your rule), though
   Zen says it follows zero retention. Keep the flag?
6. **OpenRouter free models** have no recorded data policy ("unknown"), so `no_logging`
   requests may still use them. Treat all OpenRouter free models as "may-log"?
7. **OpenCode Zen's terms** also forbid "automatically or programmatically extracts data or
   Output". Tempo only calls it with a user's own key, as an API client; confirm that is fine.
8. **GitHub Actions job**: activate it (copy to `.github/workflows/`)? It needs a private repo
   and your keys as secrets; GitHub asks that Actions be used for the project's own software
   work.
9. **GitHub Models** is retired; nothing to decide unless it returns.

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
