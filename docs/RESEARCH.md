# Research: multi-model routing platforms, free LLM APIs, and "model-of-models" techniques

_Researched September 2026. Free-tier limits change often, so re-check each provider before relying on a number here._

This document answers the first question: **what already exists on GitHub and in the market** for a system that
(1) holds API keys for many providers, (2) picks a model per query, (3) uses free / open-source models, and
(4) combines several models when one is not enough.

**Short answer:** nobody ships the whole thing as one product. There are strong open-source building
blocks for each layer, and Tempo's job is to glue them together with a good orchestrator and UX.

---

## 1. Projects closest to the whole idea

| Project | What it is | Why it matters for Tempo |
|---|---|---|
| [microsoft/JARVIS (HuggingGPT)](https://github.com/microsoft/JARVIS) | An LLM acts as the **controller**: it plans the task, picks expert models from the Hugging Face Hub by their descriptions, runs them, and writes the final answer. | This is almost exactly the "my main model decides which model to use, then answers" loop. Paper: [arXiv 2303.17580](https://arxiv.org/abs/2303.17580). Old (2023) code, but the 4-stage design (plan → select → execute → respond) is the right skeleton. |
| [ulab-uiuc/LLMRouter](https://github.com/ulab-uiuc/LLMRouter) | Open-source library with 16+ routing algorithms (kNN, SVM, MLP, matrix factorization, Elo, **GraphRouter**, RouterDC, AutoMix, Hybrid-LLM, **Router-R1**, personalized routers). Has CLI (`llmrouter train/infer/chat`), Gradio UI, and an OpenAI-compatible endpoint. | The best single place to get trained routers and a data pipeline for training your own. |
| [lm-sys/RouteLLM](https://github.com/lm-sys/routellm) | Framework for serving and evaluating routers between a strong and a weak model. OpenAI-compatible server; you call model `router-mf-0.11593` and it decides. | Reports up to 85% cost reduction while keeping 95% of GPT-4 quality on MT-Bench. Good reference for threshold calibration. |
| [vllm-project/semantic-router](https://github.com/vllm-project/semantic-router) | "Mixture-of-Models" routing layer that classifies requests by signals/intent and sends them to the right model; includes semantic cache and safety features. Apache-2.0. | Production-grade example of classifier-based routing in front of self-hosted models. |
| [NVIDIA-AI-Blueprints/llm-router](https://github.com/NVIDIA-AI-Blueprints/llm-router) | Router that classifies each prompt by **task** (code, summarization, …) or **complexity** (reasoning, creativity, domain knowledge, constraints) with `nvidia/prompt-task-and-complexity-classifier`, then routes. | Ready-made "understand the query" classifier you can reuse. |
| [katanemo Arch-Router-1.5B](https://huggingface.co/katanemo/Arch-Router-1.5B) ([paper](https://arxiv.org/abs/2506.16655), used in [Plano](https://docs.planoai.dev/guides/llm_router.html)) | A 1.5B model that maps a query to **your own** named routes ("code generation", "travel", "image editing") described in plain language. New routes need no retraining. | Excellent small "brain" for the first routing step; runs locally on CPU/GPU (GGUF available). |
| [OpenRouter](https://openrouter.ai) (commercial, not open source) | One API key → hundreds of models. Has `:free` model variants and an `openrouter/free` auto-router that picks an available free model. | The fastest way to get many free models behind one key while you build. Not a substitute for your own router. |

## 2. Gateways: one API over many providers (the "API keys" layer)

These solve "hold many keys, speak one API, retry and fall back". Don't write this layer yourself.

| Project | Notes |
|---|---|
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Python SDK + proxy. Calls 100+ providers in OpenAI format; Router with retries, fallbacks, load balancing, cost tracking. **Recommended for Tempo's provider layer.** |
| [Portkey-AI/gateway](https://github.com/Portkey-AI/gateway) | MIT, self-hostable (Docker/Node/Cloudflare Workers). Routes to 1,600+ models across 45+ providers; fallbacks, retries, weighted load balancing, semantic caching, guardrails. |
| [QuantumNous/new-api](https://github.com/QuantumNous/new-api) / [songquanpeng/one-api](https://github.com/songquanpeng/one-api) | Key management and redistribution gateways: many upstream keys, channel priorities/weights, quotas, users, usage logs. Converts between OpenAI / Claude / Gemini formats. |
| [Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers/index) | One OpenAI-compatible API routed to 15+ inference partners (Groq, Together, Fireworks, Cerebras, SambaNova, Nebius, Novita, …). |

## 3. "Connect several models" techniques (the "neural network and layers" part)

When one model is not good enough, these are the proven ways to combine models. All work with API models
because they only exchange **text**, not internal weights.

| Technique | How it works | Reported result | Code |
|---|---|---|---|
| **Cascade** (FrugalGPT) | Ask a cheap model first, score the answer, escalate to a bigger model only if the score is low. | Match the best single model with up to 98% lower cost. | [paper](https://arxiv.org/abs/2305.05176) |
| **Self-verification cascade** (AutoMix) | Small model answers, then checks its own answer; a POMDP router decides whether to escalate. | >50% compute saving at equal quality. | [automix-llm/automix](https://github.com/automix-llm/automix) |
| **Mixture-of-Agents** (MoA) | **Layers** of proposer models answer in parallel; the next layer sees their answers; an aggregator writes the final answer. | Open-source-only MoA scored 65.1% on AlpacaEval 2.0 vs 57.5% for GPT-4o. | [togethercomputer/MoA](https://github.com/togethercomputer/MoA) (Apache-2.0) |
| **Rank + fuse** (LLM-Blender) | PairRanker compares candidate answers pairwise; GenFuser merges the top-K into one answer. | Beats each individual model it blends. | [yuchenlin/LLM-Blender](https://github.com/yuchenlin/LLM-Blender) |
| **Graph neural network router** (GraphRouter, ICLR 2025) | Builds a graph of task, query and LLM nodes; predicts the quality and cost of each query→LLM edge. New models can be added without retraining. | ≥12.3% better than prior routers in the paper. | [ulab-uiuc/GraphRouter](https://github.com/ulab-uiuc/GraphRouter) |
| **LLM-as-router with RL** (Router-R1, NeurIPS 2025) | The router is itself an LLM that alternates "think" and "route" steps, calls other models mid-reasoning, folds their answers back in, and is trained with a reward that includes cost. | Beats strong baselines on 7 QA benchmarks and generalizes to unseen models. | [ulab-uiuc/Router-R1](https://github.com/ulab-uiuc/Router-R1) |
| **Embedding / semantic routing** | Embed example utterances per route; classify a new query by vector similarity. Milliseconds, no LLM call. | Very fast first-pass router. | [aurelio-labs/semantic-router](https://github.com/aurelio-labs/semantic-router) |

Surveys and lists for going deeper:
- [Dynamic Model Routing and Cascading for Efficient LLM Inference: A Survey (Moslem & Kelleher, 2026)](https://arxiv.org/abs/2603.04445): taxonomy of difficulty-based, preference-based, clustering, uncertainty, RL, multimodal and cascade routing.
- [MilkThink-Lab/Awesome-Routing-LLMs](https://github.com/MilkThink-Lab/Awesome-Routing-LLMs): curated paper list.

## 4. Where the free models come from

### 4.1 How many free models really exist?

- The [Hugging Face Hub](https://huggingface.co/models) hosts **2.4M+** models, but almost none of them have a free hosted API.
- Free **hosted API** access is much smaller: [open-free-llm-api/awesome-freellm-apis](https://github.com/open-free-llm-api/awesome-freellm-apis) (refreshed daily, live at [freellm.net](https://freellm.net)) tracks **~511 free model endpoints from 31 providers** as of 2026-09-28.
- To get to "1000+ models" you add **self-hosted open-weight models** (Ollama, vLLM, llama.cpp). Any open-weight model you can fit on your hardware is "free" to call, and has no rate limit except your GPU.

So Tempo's model pool = **free hosted APIs (~500)** + **self-hosted open models (unlimited)** + **user-supplied paid keys (optional)**.

### 4.2 Free providers (no payment needed)

Source: the auto-generated list from `cheahjs/free-llm-api-resources` (the original repo URL returned 404 during this research; a mirror such as [raullenchai/free-llm-api-resources](https://github.com/raullenchai/free-llm-api-resources) carries the generated list). That list **excludes illegitimate services**, e.g. anything that reverse-engineers a chatbot.

| Provider | Free limits (Sep 2026) | Notable free models |
|---|---|---|
| [OpenRouter](https://openrouter.ai) | 20 req/min, 50 req/day shared across all `:free` models (1,000/day after a one-time $10 top-up) | gpt-oss-120b/20b, Llama 3.3 70B, Gemma 3/4, Qwen3 Coder, Nemotron 3, GLM-4.5-Air, MiniMax M2.5, Llama Guard 4 |
| [Google AI Studio](https://aistudio.google.com) | Per model. Gemma 3 27B: 30 req/min, 14,400 req/day. Gemini 3 Flash: 5 req/min, 20 req/day | Gemini 3 / 3.1 Flash(-Lite), Gemini 2.5 Flash, Gemma 3 family |
| [Groq](https://console.groq.com) | Per model. Llama 3.1 8B: 14,400 req/day. Llama 3.3 70B, gpt-oss, Qwen3-32B: 1,000 req/day | Llama 3.x/4 Scout, gpt-oss-120b/20b, Qwen3-32B, Whisper v3 (speech-to-text), `groq/compound` (web search + code) |
| [Cerebras](https://cloud.cerebras.ai) | 30 req/min, 14,400 req/day, 1M tokens/day | gpt-oss-120b, Llama 3.1 8B (very fast) |
| [Mistral La Plateforme](https://console.mistral.ai) | 1 req/s, 500k tokens/min, 1B tokens/month | Mistral models (free "Experiment" plan) |
| [Mistral Codestral](https://codestral.mistral.ai) | 30 req/min, 2,000 req/day | Codestral (code) |
| [NVIDIA NIM](https://build.nvidia.com/explore/discover) | 40 req/min; context windows often limited | Many open models |
| [Cohere](https://cohere.com) | 20 req/min, 1,000 req/month | Command A (incl. reasoning/vision/translate), Aya |
| [GitHub Models](https://github.com/marketplace/models) | Depends on Copilot tier; very small token limits | GPT-4.1/5 family, o3/o4-mini, DeepSeek R1/V3, Llama, Phi-4, Grok 3 |
| [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai) | 10,000 "neurons"/day | ~50 models: gpt-oss, Kimi K2.5, GLM-4.7-Flash, Qwen3-30B, Llama 3.x/4, Gemma, DeepSeek R1 distill |
| [HF Inference Providers](https://huggingface.co/docs/inference-providers/en/index) | $0.10/month credit | Anything served by partners |
| [Vercel AI Gateway](https://vercel.com/docs/ai-gateway) | $5/month credit | Multi-provider |

> **These lists change within weeks.** During Phase 2 (late September 2026), OpenRouter's live `/models` listing no longer included several `:free` models that were seeded in Phase 1. Tempo's registry now carries `nemotron-3-super-120b-a12b:free`, `gemma-4-31b-it:free` and `qwen3.8-27b:free`. `tempo sync` (and the server every 6 hours) reads each provider's live model list, adds new chat models, and marks seeded models that a provider stopped listing as "no longer offered", so the router skips them.

Trial credits (one-time): Fireworks ($1), SambaNova ($5 / 3 months), Scaleway (1M tokens), Alibaba Model Studio (1M tokens per model), plus Baseten, Nebius, Novita, AI21, Upstage, NLP Cloud, Modal, Inference.net, Hyperbolic.

### 4.3 Self-hosted open-weight models (no limits except hardware)

- **Ollama**: easiest local runner, OpenAI-compatible API on `localhost:11434/v1`.
- **vLLM**: high-throughput GPU server, OpenAI-compatible.
- **llama.cpp**: CPU/GPU, GGUF quantized models; good for running the small router model.

## 5. What to avoid

- **Reverse-engineered "free GPT" projects** (for example [gpt4free](https://www.tomshardware.com/news/openai-sends-shutdown-letter-to-gpt4free)). They work by making requests through other companies' paid accounts without permission. OpenAI sent the author a takedown demand. Building a platform on them risks legal action, sudden breakage and leaking user data to unknown third parties. The reputable lists above exclude them on purpose.
- **Pooling many free accounts or rotating keys to dodge rate limits.** This breaks most providers' terms and gets keys banned. The legitimate way to scale free usage is **BYOK (bring your own key)**: each user adds their own free keys, and Tempo routes within *their* quotas.
- **Sending other people's private data to free tiers without telling them.** Free tiers often allow the provider to use prompts to improve their models, and some terms restrict serving end users. Check each provider's terms and label providers in the registry (`trains_on_data`, `allowed_for_end_users`) so the router can respect a user's privacy setting.

## 6. Takeaways for Tempo

1. **Don't build the provider layer.** Use LiteLLM (or Portkey) for keys, formats, retries and fallbacks.
2. **Your differentiator is the orchestrator**: understanding the query, choosing a strategy (single model, cascade, mixture, decomposition), watching quotas, verifying answers, and learning from outcomes.
3. **Start rule-based + embeddings, then train.** Every request you log (query, chosen model, cost, latency, judge score, user feedback) becomes training data for an MLP → GraphRouter → Router-R1 style learned router.
4. **"Connect models with neural networks" in practice means**: a learned router network that picks the models, plus MoA-style layers that pass answers between them. You cannot wire the hidden layers of two API-hosted models together.
5. **Expose Tempo as an OpenAI-compatible API and an MCP server** so other people can attach it to their tools as a "skill".

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full design.
