# Tempo

Tempo is a planned **self-routing AI platform**. You ask a question from the web app, the CLI, a terminal or the API. Tempo works out what kind of question it is, picks the best available free or open-source model, checks the answer, and brings in more models when one isn't good enough. A small **thinking window** shows each decision live.

Tempo is also meant to be **used by other tools**: it exposes itself as an OpenAI-compatible model (`tempo/auto`) and as an MCP server, so developers can attach it to their own apps and agents.

> Status: design stage. No code yet. This repository currently holds the research and architecture.

## How it works

```
question ─▶ understand ─▶ plan ─▶ route ─▶ call model(s) ─▶ verify ─┬─▶ Tempo core writes answer ─▶ user
                                                                     └─▶ not good enough: add models, mix answers ─┘
```

1. **Understand**: classify task, domain, difficulty, language and modality (rules → embeddings → small router model).
2. **Plan**: choose a strategy: single model, cascade (cheap first, escalate), mixture-of-agents, or split into sub-tasks.
3. **Route**: score every healthy model with free quota left, by predicted quality, speed and scarcity.
4. **Verify**: tests, format checks, self-check, judge model, agreement between models.
5. **Escalate**: when confidence is low, run several models from different families in layers and merge their answers.
6. **Synthesize**: Tempo's own core model writes one consistent final answer.
7. **Learn**: logged outcomes and user feedback train better routers over time.

## Documents

- [docs/RESEARCH.md](docs/RESEARCH.md): existing GitHub projects (routers, gateways, model-mixing methods), the free LLM API providers and their limits, and what to avoid.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the full design: components, request lifecycle, thinking-window events, the neural router, using Tempo as a skill (API / MCP / CLI), security, tech stack, repo layout and roadmap.

## Planned stack

Python · FastAPI · [LiteLLM](https://github.com/BerriAI/litellm) · Redis · Postgres + pgvector · Ollama / vLLM · PyTorch · Next.js · Typer + Rich
