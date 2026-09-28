# Tempo

Tempo is a **self-routing AI platform**. You ask a question from the web app, the CLI or the API. Tempo works out what kind of question it is and picks the best free or open-source model that is available. It checks the answer, and when the answer is weak it brings in more models to fix it, merge several drafts, or split the job into parts. A small **thinking window** shows every stage live: its job, the model, the reason, the time taken and the free quota left.

Tempo is also meant to be **used by other tools**: it exposes itself as an OpenAI-compatible model (`tempo/auto`), so any OpenAI client can use it by changing the base URL.

> **Status: Phase 2 (Smart) is done.** The staged engine (draft → check → fix → merge/polish, with early stop and budgets), the quota manager, answer checking, cascade, mixture and decompose strategies, the embedding classifier, the semantic cache, measured skill scores, registry sync and health checks, users with their own provider keys, and the usage dashboard all work. Laya runs as the fast decision-maker in shadow mode, and every question is logged for tuning. The learned router, the MCP server and the SDK come next (see the [roadmap](docs/ARCHITECTURE.md#11-roadmap)).

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                       # core
pip install -e ".[embeddings]"         # optional: embedding classifier + semantic cache (fastembed, CPU)
pip install -e ".[laya]"               # optional: Laya decision-maker (pulls in PyTorch)

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
| Cerebras | `CEREBRAS_API_KEY` | https://cloud.cerebras.ai |
| Google AI Studio | `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| OpenRouter (free models) | `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| Ollama (local) | `OLLAMA_API_BASE=http://localhost:11434` | https://ollama.com/download |

Installed Ollama models are discovered automatically at startup. The seed model list, skill priors, free limits and each provider's training terms live in [`tempo/models.yaml`](tempo/models.yaml); set `TEMPO_MODELS_FILE` to use your own copy. The server refreshes each provider's model list every 6 hours, and `tempo sync` does it on demand.

## Using Tempo

### Command line

```bash
tempo ask "What is 17% of 2,340?"                 # thinking window on stderr, answer on stdout
tempo ask --mode best "Prove that √2 is irrational"
tempo ask --private "Summarize this" < notes.txt  # local models only
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
| `tempo eval [--model ID] [--task code]` | Measure models on the probe set; the router then blends measured skills into its scores |
| `tempo users add NAME` / `list` / `remove` | Create users; each gets a Tempo API key (shown once) |
| `tempo keys add groq [--user NAME]` / `list` / `remove` | Store a provider key, encrypted, after checking it with the provider |
| `tempo export-laya --out DIR [--include-unclear]` | Export logged decisions as a Laya fine-tuning dataset |
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

- **Conditions** go in the `tempo` field: `mode`, `privacy` (`"local_only"`), `allow_providers`, `trace`, `max_stages` (1–50), `time_budget_s`, `quota_budget`, `max_parallel`, `strategy` (`single`, `cascade`, `mixture`, `decompose`).
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

### Tuning Laya on your own logs

```bash
tempo export-laya --out laya-dataset          # train.jsonl + test.jsonl + README
# fine-tune with Laya's notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb
export TEMPO_LAYA_MODEL=/path/to/checkpoint   # use the tuned checkpoint (still in shadow mode)
tempo laya compare                            # after a few hundred more questions
export TEMPO_LAYA_TAKEOVER="should_stop=auto, next_model=auto"   # Laya takes over where it wins
```

The labels come from outcomes: how many stages an answer really needed, judge scores, 👍/👎, and whether later stages improved the answer. Only text from providers marked `training_on_outputs: yes` in `models.yaml` is exported by default; `--include-unclear` adds `unclear` ones after you have read their terms (`tempo terms`), and `no` is never exported. On a CPU, Laya takes 0.4–1.2 s per decision group, so it stays in shadow mode (timed-out predictions are still logged, as `late`). Taking over needs a GPU.

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
| `TEMPO_LAYA_TIMEOUT_MS` | `200` | Rules decide when Laya is slower than this |
| `TEMPO_LAYA_MIN_CONFIDENCE` | `0.6` | Below this, the rules decide even after a takeover |
| `TEMPO_LAYA_DEVICE` | auto | For example `cuda` or `cpu` |
| `TEMPO_EMBEDDINGS` | `auto` | `off` uses keyword rules only (and turns off the semantic cache) |
| `TEMPO_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Any fastembed model |
| `TEMPO_CACHE` / `TEMPO_CACHE_TTL` | `1` / `86400` | Semantic cache on/off and max age in seconds |
| `TEMPO_SECRET_KEY` | generated | Key-vault secret (otherwise a 0600 `secret.key` file in the data dir) |
| `TEMPO_SYNC_INTERVAL` | `21600` | Seconds between registry syncs (`0` = off) |
| `TEMPO_API_KEY` | none | Admin key for the API and web app |
| `TEMPO_REQUEST_TIMEOUT` / `TEMPO_MAX_ATTEMPTS` | `60` / `4` | Per-call timeout and fallback attempts |

## Development

```bash
pip install -e ".[dev]"
pytest          # 199 tests; the mock models cover early stop, parallel stages, Laya shadow mode,
                # fallback and timeout, quota budgets and the event stream; some tests make real
                # LiteLLM calls against a local fake provider server
ruff check . && ruff format --check .
```

## Documents

- [docs/RESEARCH.md](docs/RESEARCH.md): existing GitHub projects (routers, gateways, model-mixing methods), the free LLM API providers and their limits, and what to avoid.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the full design: components, request lifecycle, the staged engine, Laya, thinking-window events, the neural router, using Tempo as a skill (API / MCP / CLI), security, tech stack, the roadmap and the planned **Tempo Tune** phase.
