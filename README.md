# Tempo-server

**Tempo-server is open models plus the system that runs and trains them.** It routes every question to the best free or open model, checks the answer, fixes it when it is weak, shows every step, and trains its own small open models (Tempo-Router, Tempo-Judge, Tempo-Core) from the answers that passed, all on an ordinary CPU ([plan](docs/TEMPO_MODELS.md); nothing trained yet).

Tempo is a **self-routing AI platform**. You ask a question from the web app, the CLI or the API. Tempo works out what kind of question it is and picks the best free or open-source model that is available. It checks the answer, and when the answer is weak it brings in more models to fix it, merge several drafts, or split the job into parts. A small **thinking window** shows every stage live: its job, the model, the reason, the time taken and the free quota left.

Tempo is also meant to be **used by other tools**: it exposes itself as an OpenAI-compatible model (`tempo/auto`), so any OpenAI client can use it by changing the base URL.

> **Status: Phase 2 (Smart) is done.** The staged engine (draft → check → fix → merge/polish, with early stop and budgets), the quota manager, answer checking, cascade, mixture and decompose strategies, the embedding classifier, the semantic cache, measured skill scores, registry sync and health checks, users with their own provider keys, and the usage dashboard all work. Laya runs as the fast decision-maker in shadow mode, and every question is logged for tuning. The learned router, the MCP server and the SDK come next (see the [roadmap](docs/ARCHITECTURE.md#11-roadmap)).
>
> **Software only.** Everything Tempo runs works on an ordinary CPU computer or a free cloud service; nothing needs a GPU. Free notebooks (Kaggle) are used only for offline training jobs, such as fine-tuning Laya ([the rule](docs/ARCHITECTURE.md#1-design-principles)).

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                       # core
pip install -e ".[embeddings]"         # optional: embedding classifier + semantic cache (fastembed, CPU)
pip install -e ".[laya]"               # optional: Laya decision-maker on CPU (PyTorch + ONNX Runtime)

cp .env.example .env        # then add at least one free key, e.g. GROQ_API_KEY
tempo models                # see which models are ready
tempo ask "Explain the difference between TCP and UDP"
tempo serve                 # web app on http://127.0.0.1:8000
```

No keys yet? `TEMPO_ENABLE_MOCK=1 tempo serve` runs offline demo models. One of them always fails on purpose, so you can watch the fallback happen.

### Providers (all have free tiers)

| Provider | Set | Get it |
|---|---|---|
| Groq | `GROQ_API_KEY` | https://console.groq.com/keys |
| Cerebras (off by default: trial, needs a payment method; `TEMPO_ENABLE_PROVIDERS=cerebras`; never used for eval or collect) | `CEREBRAS_API_KEY` | https://cloud.cerebras.ai |
| Google AI Studio | `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| OpenRouter (free models) | `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| Cloudflare Workers AI (10,000 neurons/day) | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com/profile/api-tokens |
| Cohere (off by default: each user adds their own trial key; answers only, never judges, never eval or collect) | added per user with `tempo keys add cohere` | https://dashboard.cohere.com/api-keys |
| Mistral (free plan; limits read from response headers) | `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys |
| NVIDIA API catalog (off by default; the owner's private testing only, never other users or demo mode; `TEMPO_ENABLE_PROVIDERS=nvidia`) | `NVIDIA_API_KEY` | https://build.nvidia.com |
| OpenCode Zen (off by default, `TEMPO_ENABLE_PROVIDERS=opencode`; free models, each user's own key, answering only) | added per user with `tempo keys add opencode` | https://opencode.ai/auth |
| Ollama (local) | `OLLAMA_API_BASE=http://localhost:11434` | https://ollama.com/download |

GitHub Models is not offered: GitHub retired it on 30 July 2026 ([docs](https://docs.github.com/en/github-models), checked 2026-09-28).

Model lists are read live from each provider (public lists without a key: OpenRouter, NVIDIA, OpenCode Zen). Every model gets a type (chat, code, vision, speech-to-text, text-to-speech, safety, embedding, reranker, decision); only chat-capable ones get chat requests. `tempo terms` shows each provider's training verdict and what its free tier may do with prompts; `tempo terms --check` re-reads the terms pages and reports quotes that changed (`pip install -e ".[terms]"` for NVIDIA's PDF).

Installed Ollama models are discovered automatically at startup. The seed model list, skill priors, free limits and each provider's training terms live in [`tempo/models.yaml`](tempo/models.yaml); set `TEMPO_MODELS_FILE` to use your own copy. The server refreshes each provider's model list every 6 hours, and `tempo sync` does it on demand.

### Provider terms (may outputs be training data?)

From `tempo terms` (quotes re-checked on the providers' pages with `tempo terms --check`, 2026-09-28). "Yes" sources are the only ones Tempo exports as training data.

| Provider | Training on outputs | Free tier's use of prompts | Tempo's use |
|---|---|---|---|
| Ollama (local) | by each model's licence: Apache-2.0 / MIT = yes | stays on your computer | default for private and training runs |
| Mistral (free plan) | yes, for text outputs | may train (opt out in the console, then `TEMPO_OPTED_OUT=mistral`) | answering; terms re-read before every export |
| Cloudflare Workers AI | by each model's licence (Cloudflare adds no limit) | not kept, not trained on | answering |
| Groq | unclear (until confirmed in writing) | unknown | answering |
| OpenRouter (free models) | unclear (until confirmed in writing) | treated as may log | answering; `openrouter/free` last |
| Google AI Studio | no | may train | answering |
| Cohere (trial) | no | may train | users' own keys only; answers only, never judges, eval or collect |
| NVIDIA (trial) | no | may train | off; the owner's private testing only |
| OpenCode Zen | no | per model | off; users' own keys, answering only |
| Cerebras (trial) | unclear | unknown | off; never eval or collect |

## Using Tempo

### Command line

```bash
tempo ask "What is 17% of 2,340?"                 # thinking window on stderr, answer on stdout
tempo ask --mode best "Prove that √2 is irrational"
tempo ask --private "Summarize this" < notes.txt  # local models only
tempo ask --no-logging "Summarize this" < notes.txt  # never a free tier that may log or train on prompts
tempo ask -s 20 --time-budget 300 "Plan a 5-part course on SQL, with exercises"  # big job
tempo ask --strategy mixture "Compare REST and GraphQL for a mobile app"
tempo ask --json "hi"                              # one JSON object with answer + trace
tempo chat                                         # interactive; /mode fast, /clear, /exit
```

Modes: `auto` (balanced), `fast`, `best` (uses scarce strong models and a higher pass mark), `private` (local only).

Other commands:

| Command | What it does |
|---|---|
| `tempo models` | Which models are ready, and why the others aren't (no key, quota used up, no longer offered, …) |
| `tempo sync` | Refresh provider model lists now and report each provider's health |
| `tempo models --free [--json]` | Live free-model catalog: provider, model, type, context, max output, inputs, tools, limits, data policy, health, last check and status |
| `tempo eval [--model ID] [--task code]` | Measure models on the probe set; the router then blends measured skills into its scores |
| `tempo users add NAME` / `list` / `remove` | Create users; each gets a Tempo API key (shown once) |
| `tempo users consent NAME [--on\|--off]` / `forget NAME` | A user opts in to (or withdraws from) training use of their questions, off by default; `forget` deletes their logged questions. Also `PUT /api/consent` and `DELETE /api/data` |
| `tempo keys add groq [--user NAME]` / `list` / `remove` | Store a provider key, encrypted, after checking it with the provider |
| `tempo collect [--yes-only] [--estimate \| --status \| --list]` | Make Laya training data from openly licensed public questions, slowly and within every free limit; resumable. `--yes-only` uses only models whose outputs may be training data (local Apache-2.0/MIT models) |
| `tempo export-laya --out DIR [--include-unclear]` | Export logged decisions as a Laya fine-tuning dataset |
| `tempo export-sft --out DIR` | Question → checked final answer, for fine-tuning Tempo-Core (only "yes" rows, licence and source on each) |
| `tempo export-pairs --out DIR` | Question, chosen (answer that passed) and rejected (draft that failed), for DPO (only "yes" rows) |
| `tempo terms` | Whether each provider's outputs may be used for training: verdict, link and exact sentences |
| `tempo laya status` / `tempo laya compare` | Laya's state per decision; Laya vs rules on held-out questions |

### Web app

`tempo serve`, then open http://127.0.0.1:8000.

- **Ask:** a prompt box with a mode switch and a ⚙ settings panel for the stage, time and free-quota budgets. Each answer gets a live thinking window (auto-scrolling, collapsible) with a stage progress bar. The first draft streams right away, later stages replace it live, and a 👍/👎 under each answer is saved for tuning.
- **Models:** providers and models, ready or not and why, with measured skill scores and health.
- **Usage:** questions, pass rate, median and p95 time, average stages, free requests used, feedback, the models used, why questions stopped, free quota left today, and how often Laya agrees with the rules. Users see only their own questions.
- **Keys:** add your own provider keys (bring your own key). A key is checked with the provider before it is saved, stored encrypted, and never sent back to the page.
- **Developers:** copy-paste snippets for the API.

### OpenAI-compatible API

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
reply = client.chat.completions.create(
    model="tempo/auto",  # or tempo/fast, tempo/best, tempo/private, or a model id from `tempo models`
    messages=[{"role": "user", "content": "Explain recursion in one paragraph."}],
    extra_body={"tempo": {"allow_providers": ["groq", "cerebras"], "max_stages": 3, "trace": True}},
)
print(reply.model)  # the model that wrote the final answer
print(reply.choices[0].message.content)
print(reply.tempo["trace"])  # thinking-window events
```

- **Conditions** go in the `tempo` field: `mode`, `privacy` (`"local_only"`, or `"no_logging"`: never a model whose free tier may log or train on prompts), `allow_providers`, `trace`, `max_stages` (1–50), `time_budget_s`, `quota_budget`, `max_parallel`, `strategy` (`single`, `cascade`, `mixture`, `decompose`).
- **Streaming** works (`stream=True`). OpenAI clients can't take back text, so a multi-stage question streams the checked final answer. With `max_stages: 1` it streams live from the model. Trace events arrive as chunks with empty `choices`, and model reasoning arrives as `delta.reasoning_content`.
- `POST /api/ask` streams every engine event as server-sent events, including live drafts and `answer_reset` when a later stage replaces a draft. The web app uses it. `POST /api/feedback` records 👍/👎, and `GET /api/usage?hours=24` returns the dashboard numbers.
- **Auth:** with no users and no `TEMPO_API_KEY`, the server is open (local mode). Once a user exists, every `/v1/*` and `/api/*` request needs `Authorization: Bearer <their Tempo key>` (the web app asks for it). `TEMPO_API_KEY` is an admin key that sees everything.

## How it works

```
question ─▶ understand ─▶ plan ─▶ draft ─▶ check ─┬─ passes ─▶ final answer
            (rules,        (strategy,  (1 model, or  │
             embeddings,    stage       2-3 from      └─ fails ─▶ fix / merge / polish ─▶ check …
             Laya)          budget)     different                 (until it passes or a budget runs out)
                                        families)
```

1. **Understand** ([`analyzer.py`](tempo/analyzer.py), [`embeddings.py`](tempo/embeddings.py)): task, complexity, script, needs and token estimates. Keyword rules come first; an embedding kNN vote overrides them only when the rules aren't sure. Near-identical recent questions are answered from the semantic cache.
2. **Plan and rank** ([`router.py`](tempo/router.py), [`quota.py`](tempo/quota.py), [`evals.py`](tempo/evals.py)): drop models that can't take the request (no key, not installed, cooling down, free quota used up, no longer offered, context too small, not local in private mode). Score the rest by predicted quality (priors blended with measured and live judge scores), quota scarcity and latency, weighted by mode. Pick a strategy: single, cascade, mixture or decompose.
3. **Run stages** ([`pipeline.py`](tempo/pipeline.py)): draft, check ([`checks.py`](tempo/checks.py): heuristics, plus a judge from a different model family), then fix, merge or polish until the answer passes. Stop at the first pass, or when the stage, time or free-quota budget runs out. Calls go through [LiteLLM](https://github.com/BerriAI/litellm) with automatic fallback; a rate limit or error cools that model down and moves to the next one.
4. **Decide fast with Laya** ([`laya_decider.py`](tempo/laya_decider.py)): before stage 1, Laya predicts the task type, difficulty, strategy and stage budget. After each check it scores the answer and says stop or continue, and before each stage it picks a model from a shortlist of at most 10. It starts in **shadow mode**: Laya predicts, the rules decide, and both are logged. If Laya is missing, errors, or takes longer than 200 ms, the rules decide.
5. **Show and log everything** ([`events.py`](tempo/events.py), [`store.py`](tempo/store.py)): every step is an event with a one-line summary, rendered the same way by the web app, CLI and API. Every question is logged to SQLite (`~/.tempo/tempo.db`): decisions with Laya's predictions and probabilities, stages, calls, check results, times, quota used, the final answer and feedback.

The full design, with the stage jobs, events and Laya details, is in [docs/ARCHITECTURE.md §4.11–4.12](docs/ARCHITECTURE.md#411-the-staged-engine-built-in-phase-2).

### Laya on CPU, and tuning it on your own decisions

Laya runs on an ordinary CPU. In shadow mode (the default) Tempo asks it in the background, so it adds no time to an answer; only decisions you hand to Laya are waited for, within a time limit Tempo measures on your machine when Laya loads. Measured on a 4-core CPU: shadow mode costs nothing, and a taken-over decision costs about 0.4–0.8 s per stage ([docs/LAYA_CPU.md](docs/LAYA_CPU.md), which also explains why INT8 is not the default).

The stock checkpoints are near chance on Tempo's decisions, so fine-tune one first. The full walk-through is in [docs/LAYA_TUNING.md](docs/LAYA_TUNING.md):

```bash
tempo collect --estimate                      # how long, at your keys' free limits
tempo collect                                 # public, openly licensed questions; stop and resume any time
tempo export-laya --out laya-dataset          # train.jsonl + test.jsonl + README (licences listed)
# fine-tune with Laya's notebook on Kaggle's free GPUs (offline; the only GPU step)
export TEMPO_LAYA_MODEL=/path/to/checkpoint   # the tuned checkpoint, on CPU, still in shadow mode
tempo laya compare                            # after a few hundred more questions
export TEMPO_LAYA_TAKEOVER="should_stop=auto, next_model=auto"   # Laya takes over where it wins
```

The labels come from outcomes: how many stages an answer really needed, judge scores, 👍/👎, and whether later stages improved the answer. A row is exported only if every model that answered or judged its question belongs to a provider marked `training_on_outputs: yes`; `--include-unclear` adds `unclear` ones after you have read their terms (`tempo terms`), and `no` is never exported.

## Settings

All optional; put them in `.env` or the environment.

| Variable | Default | Meaning |
|---|---|---|
| `TEMPO_DATA_DIR` | `~/.tempo` | Where the SQLite log, quota counters, users and the key-vault secret live (`memory` keeps nothing) |
| `TEMPO_LOG` | `1` | Log every question for tuning |
| `TEMPO_MAX_STAGES` | `5` | Most stages per question (up to 50) |
| `TEMPO_TIME_BUDGET` | `60` | Seconds per question |
| `TEMPO_QUOTA_BUDGET` | `12` | Free provider requests per question (local models are free) |
| `TEMPO_MAX_PARALLEL` | `3` | Models per parallel stage |
| `TEMPO_JUDGE` | `1` | Use a judge model in the check stage |
| `TEMPO_LAYA` | `auto` | `auto` (load if installed), `on` (also for one-shot CLI runs), `off` |
| `TEMPO_LAYA_MODEL` | stock checkpoints | A fine-tuned Laya checkpoint (folder or Hub repo) for every decision |
| `TEMPO_LAYA_TAKEOVER` | all `shadow` | Per decision: `shadow`, `laya` or `auto`, e.g. `should_stop=auto, task_type=laya` or `all=auto` |
| `TEMPO_LAYA_TIMEOUT_MS` | `auto` | How long an answer waits for a taken-over decision; `auto` measures it on this machine at load |
| `TEMPO_LAYA_MIN_CONFIDENCE` | `0.6` | Below this, the rules decide even after a takeover |
| `TEMPO_LAYA_BACKEND` | `torch` | `torch` (fp32), `onnx` (fp32, same answers) or `onnx-int8` (faster, but changes answers) |
| `TEMPO_LAYA_CHECKPOINT` | `english` | Stock checkpoint: `english` or `multilingual` (2.6× faster, a different model) |
| `TEMPO_LAYA_THREADS` | up to 4 | CPU threads for Laya |
| `TEMPO_EMBEDDINGS` | `auto` | `off` uses keyword rules only (and turns off the semantic cache) |
| `TEMPO_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Any fastembed model |
| `TEMPO_CACHE` / `TEMPO_CACHE_TTL` | `1` / `86400` | Semantic cache on/off and max age in seconds |
| `TEMPO_SECRET_KEY` | generated | Key-vault secret (otherwise a 0600 `secret.key` file in the data dir) |
| `TEMPO_SYNC_INTERVAL` | `21600` | Seconds between registry syncs (`0` = off) |
| `TEMPO_API_KEY` | none | Admin key for the API and web app |
| `TEMPO_MIN_PUBLIC_SHARE` / `TEMPO_MAX_SELF_SHARE` | `0.3` / `0.3` | Training data mix: at least this share from public or human data, at most this share written by an earlier Tempo-Core (exports warn) |
| `TEMPO_ENABLE_PROVIDERS` / `TEMPO_OPTED_OUT` | none | Providers that are off by default to turn on (e.g. `nvidia`); providers whose "train on my data" setting you turned off (e.g. `mistral`) |
| `TEMPO_REQUEST_TIMEOUT` / `TEMPO_MAX_ATTEMPTS` | `60` / `4` | Per-call timeout and fallback attempts |

## Development

```bash
pip install -e ".[dev]"
pytest          # 281 tests; the mock models cover early stop, parallel stages, Laya shadow mode,
                # fallback and timeout, quota budgets and the event stream; some tests make real
                # LiteLLM calls against a local fake provider server
ruff check . && ruff format --check .
```

## Documents

- [CLAUDE.md](CLAUDE.md): the owner's rules every working session follows (software only, free only, live model lists, keys, provider terms, training data).
- [docs/STATUS.md](docs/STATUS.md): what is done, in progress, blocked and next, and what to run once provider keys exist.
- [docs/TEMPO_MODELS.md](docs/TEMPO_MODELS.md): Tempo's own open models (Tempo-Router, Tempo-Judge, Tempo-Core, Tempo Tune add-ons): data, training plan, promotion gate, collapse protection, release.
- [docs/USE_CASES.md](docs/USE_CASES.md): 20 scenarios Tempo is for, what each still needs, and its roadmap phase.
- [docs/PUBLISHING.md](docs/PUBLISHING.md): licence options, keys, the demo, and the checklist before going public. See also [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [examples/](examples/).
- [docs/COLLECT_ANYWHERE.md](docs/COLLECT_ANYWHERE.md): step-by-step `tempo collect` on a Windows computer (local open-licence models), and as a scheduled GitHub Actions job that resumes across runs.
- [docs/RESEARCH.md](docs/RESEARCH.md): existing GitHub projects (routers, gateways, model-mixing methods), the free LLM API providers and their limits, and what to avoid.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the full design: components, request lifecycle, the staged engine, Laya, thinking-window events, the neural router, using Tempo as a skill (API / MCP / CLI), security, tech stack, the roadmap and the planned **Tempo Tune** phase.

## Licence

Apache-2.0 ([LICENSE](LICENSE), [NOTICE](NOTICE)). Copyright 2026 The Tempo-server authors. Models inherit their base model's licence, and their model cards credit the datasets they were trained on.
