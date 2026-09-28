# Status

_Last updated 2026-09-28, at the end of step 1 (setup and checks). Branch:
`claude/multi-model-ai-platform-o0fx9v`._

Read [CLAUDE.md](../CLAUDE.md) first: it has the rules every session follows.

## Done

- **Rules saved** in `CLAUDE.md` at the repo root.
- **Full test suite:** 229 passed (was 223; 6 new tests), `ruff check` and `ruff format --check`
  clean. Runs offline in about 15 s.
- **Full mock run** (`TEMPO_ENABLE_MOCK=1`, demo models) of five kinds of question, through the
  CLI (`tempo ask`, `tempo ask --json`) and the web page (`tempo serve`, driven with headless
  Chromium): a greeting, a coding task, a maths word problem, a 42 KB report to summarise (about
  10,500 tokens), and questions in Gujarati and Hindi. Checked for each: the stage events
  (received → analyze → plan → stage_start / call_* / check / stage_end → answer_final → done),
  the stage dots and summary line on the web page, the fallback after the demo rate-limit error
  (`mock/flaky` → `mock/smart`, then the cool-down skips it), the replacement of the shown answer
  by a later stage, the final answer and model badge, 👍/👎, the usage API, and the semantic cache
  on a repeated question. No JavaScript errors.
- **Fixed during the mock run:**
  - The maths word problem ("A train travels 240 km in 3 hours…") was classified as `chat` and
    got a single draft. The analyzer now treats two or more numbers plus a quantity word
    (average, speed, how many, per hour, …) as maths. The first version of that rule backtracked
    badly on long documents (tests went from 15 s to 4 minutes); it is now anchored and fast.
  - Answers in the wrong language passed: a Latin-script answer to a Gujarati or Hindi question
    scored 0.72 with heuristics only and was sent. The wrong-script issue now weighs as two soft
    issues (except for code tasks, where answers are mostly Latin letters), so it fails `auto`
    without a judge and the answer is rewritten. The system prompt now asks models to reply in the
    question's language.
  - The key-vault warning said "No TEMPO_DATA_DIR" when `TEMPO_DATA_DIR=memory` was set; it now
    says what is actually the case.
- **`tempo sync` with public data only** (no keys): OpenRouter's public model list is read for
  real. On 2026-09-28 it listed 460 models, 21 of them zero-priced; Tempo kept 16 (3 seeds, 13
  new) and correctly left out 2 music models (Lyria), a content-safety guard, OpenRouter's own
  `openrouter/free` meta-router and one `stealth/` preview model. **Fixed in sync:**
  - `thinkingmachines/inkling*` was marked as a reasoning model only because its organisation's
    name contains "think". Reasoning now comes from OpenRouter's `supported_parameters`, and is
    otherwise guessed from the model's own name.
  - Size and family now fall back to `hugging_face_id` when the id has none (for example
    Nemotron 3.5 Lightning is 30B with 3B active, not the 30B default guess).
  - An unknown family now falls back to the organisation, so the judge's "different family"
    rule no longer treats every unfamiliar model as one family.
  - Models past their `expiration_date` are not added.

## In progress

Nothing. Step 1 is finished; waiting for step 2 from the owner.

## Blocked: needs key

No provider keys exist yet in this environment. These were built and tested only with mocks or
local fake servers:

| Item | Needs | Tested so far with |
|---|---|---|
| Real answers from Groq, Google AI Studio, OpenRouter | `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | mock models; a local fake OpenAI-style server through LiteLLM |
| Cerebras (trial credits only, see decisions) | `CEREBRAS_API_KEY` | mocks |
| Live model lists and health for Groq, Cerebras, Google | the same keys (their `/models` need a key) | `httpx.MockTransport` in tests |
| Live rate-limit headers correcting the quota counters | any provider key | mocked headers |
| `tempo keys add` checking a key with the provider | a real key | mocked responses |
| `tempo eval` (measured skills) | at least one key | mock models |
| `tempo collect` (Laya training data) | a key for a provider marked `yes` | mocks; note no current provider is `yes` |

## What to run once keys exist

Keys go into this environment's API credentials (the proxy adds them); never paste them into
files, commands or logs.

```bash
. .venv/bin/activate
tempo sync                      # every configured provider: listed / new / no longer listed / key ok
tempo models                    # which models are ready
tempo ask "hi there!"           # then the other four step-1 questions, without TEMPO_ENABLE_MOCK
tempo ask "A train travels 240 km in 3 hours, then 180 km in 2 hours. What is its average speed in km/h?"
tempo ask "ગુજરાતની રાજધાની કઈ છે?"
tempo eval --task math          # measured skills (uses free quota; not with Cerebras, see rule 6)
tempo terms                     # re-read each provider's training verdict
```

Also check that a real 429 from a provider cools the model down and falls back, as the mock one
does.

## Next

- Step 2, when the owner sends it.
- Rule 3 follow-ups noticed during step 1 (not started):
  - Free limits in `tempo/models.yaml` point at `docs/RESEARCH.md` as a whole, not a source link
    and date per fact. Each hand-entered limit should carry its own link and date.
  - OpenRouter's free limits (20/min, 50/day shared) are hand-entered; with a key they can be read
    live from OpenRouter's key endpoint.
  - NVIDIA's public model list is not used yet (there is no NVIDIA provider in the registry).
  - OpenRouter's per-model endpoint status (uptime) is public and not used yet.
- Rule 6 is followed by hand today, not enforced in code: `tempo eval` and `tempo collect` do
  not yet refuse Cerebras (it has no key here, so it cannot be used anyway). Add an exclusion
  before any Cerebras key is added.
- Sync priors for unfamiliar models are all about 0.69 strength when the size is unknown; `tempo
  eval` fixes that once keys exist.

## Decisions for the owner

1. **Cerebras** no longer has a renewing free tier: its trial needs a payment method and gives
   $5 of credits for 30 days. Rule 2 says no paid API may be required. Tempo still lists it
   (marked "trial") but never requires it. Keep it, or drop it from the registry?
2. **Training data:** no provider is marked `yes` today (Google AI Studio `no`; Groq, Cerebras,
   OpenRouter and local Ollama `unclear`; Ollama depends on each model's licence), so `tempo collect` / `tempo export-laya` can produce no rows until a
   provider is judged `yes` or local open-weight models (Ollama) are used. Which route?
3. **Preview and domain-specific free models:** sync adds OpenRouter models such as
   `dots-3-note-preview` (preview models may log prompts) and `ling-3.0-flash-fin` / `-sante`
   (finance and health specialists), with the same generic priors as general models. Skip
   `*-preview` models by default, and/or keep domain specialists out of general routing?
4. **Wrong-language answers** now fail `auto` without a judge (step-1 fix). If a mixed-language
   answer (for example English technical terms in a Hindi answer) turns out to fail too often
   with real models, the weight can go back down.
5. **Code answers without a code block** still pass when the judge likes them (it is a soft
   issue, since some code questions are conceptual). Make it stronger for "write a function"
   style requests?

## How the checks were run (for the next session)

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q && ruff check . && ruff format --check .
export TEMPO_ENABLE_MOCK=1 TEMPO_DATA_DIR=memory TEMPO_SYNC_INTERVAL=0
tempo ask "hi there!"                        # and the other questions above
tempo serve --port 8766                      # web page; drive it with Playwright
                                             # (executable_path=/opt/pw-browsers/chromium)
env TEMPO_DATA_DIR=memory tempo sync         # public OpenRouter list, no key
```
