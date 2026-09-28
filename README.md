# Tempo

Tempo is a **self-routing AI platform**. You ask a question from the web app, the CLI or the API. Tempo works out what kind of question it is, picks the best available free or open-source model, and switches to another model automatically if one fails or runs out of free quota. A small **thinking window** shows each decision live.

Tempo is also meant to be **used by other tools**: it exposes itself as an OpenAI-compatible model (`tempo/auto`), so any OpenAI client can use it by changing the base URL.

> **Status: Phase 1 (MVP).** Routing, automatic fallback, the thinking window, the web app, the CLI and the OpenAI-compatible API work. Answer verification, multi-model mixing, quota tracking and the learned router come in later phases (see the [roadmap](docs/ARCHITECTURE.md#11-roadmap)).

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

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

Installed Ollama models are discovered automatically at startup. The model list, skill scores and free limits live in [`tempo/models.yaml`](tempo/models.yaml); set `TEMPO_MODELS_FILE` to use your own copy.

## Using Tempo

### Command line

```bash
tempo ask "What is 17% of 2,340?"                 # thinking window on stderr, answer on stdout
tempo ask --mode best "Prove that √2 is irrational"
tempo ask --private "Summarize this" < notes.txt  # local models only
tempo ask --json "hi"                              # one JSON object with answer + trace
tempo chat                                         # interactive; /mode fast, /clear, /exit
```

Modes: `auto` (balanced), `fast`, `best` (uses scarce strong models), `private` (local only).

### Web app

`tempo serve`, then open http://127.0.0.1:8000. It has a prompt box with mode switch, a live thinking window per answer (collapsible, auto-scrolling, with the model's own reasoning when it shares it), a Models page showing which providers are configured, and a Developers page with copy-paste snippets.

### OpenAI-compatible API

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="unused")
reply = client.chat.completions.create(
    model="tempo/auto",  # or tempo/fast, tempo/best, tempo/private, or a model id from `tempo models`
    messages=[{"role": "user", "content": "Explain recursion in one paragraph."}],
    extra_body={"tempo": {"allow_providers": ["groq", "cerebras"], "trace": True}},
)
print(reply.model)  # the model Tempo routed to
print(reply.choices[0].message.content)
print(reply.tempo["trace"])  # thinking-window events
```

- Conditions go in the `tempo` field: `mode`, `privacy` (`"local_only"`), `allow_providers`, `trace`.
- Streaming works (`stream=True`). Trace events ride along as chunks with empty `choices`, and model reasoning arrives as `delta.reasoning_content`.
- `POST /api/ask` streams every engine event as server-sent events. The web app uses it.
- Set `TEMPO_API_KEY` to require `Authorization: Bearer <key>` on `/v1/*` and `/api/*`.

## How it works (Phase 1)

```
question ─▶ analyze ─▶ rank models ─▶ call best ─┬─▶ answer streams back
                                                  └─▶ failed / rate-limited / empty: next model
```

1. **Analyze** ([`analyzer.py`](tempo/analyzer.py)): task (code, math, writing, …), complexity, script (e.g. Gujarati, Devanagari), needs (reasoning, JSON, images, long context), token estimates.
2. **Rank** ([`router.py`](tempo/router.py)): drop models that can't take the request (no key, not installed, cooling down, context or free tokens/min too small, no image support, not local in private mode). Then score each model: `quality − scarcity − latency`, weighted by mode.
3. **Call with fallback** ([`engine.py`](tempo/engine.py)): stream from the best model through [LiteLLM](https://github.com/BerriAI/litellm). On a rate limit, error, timeout or empty answer, cool that model down ([`health.py`](tempo/health.py)) and try the next one. A rejected API key cools down the whole provider.
4. **Show everything** ([`events.py`](tempo/events.py)): every step is an event with a one-line human summary. The web app, CLI and API all render the same stream.

## Development

```bash
pip install -e ".[dev]"
pytest          # 85 tests; includes real LiteLLM calls against a local fake provider server
ruff check . && ruff format --check .
```

## Documents

- [docs/RESEARCH.md](docs/RESEARCH.md): existing GitHub projects (routers, gateways, model-mixing methods), the free LLM API providers and their limits, and what to avoid.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the full design: components, request lifecycle, thinking-window events, the neural router, using Tempo as a skill (API / MCP / CLI), security, tech stack and roadmap.
