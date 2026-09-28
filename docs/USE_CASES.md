# What Tempo is for: 20 scenarios

_Step 3, 2026-09-28._ Each scenario has one example, what works today, what Tempo still needs,
and the roadmap phase that brings it ([ARCHITECTURE.md §11](./ARCHITECTURE.md#11-roadmap):
Phase 3 Learning, Phase 4 Platform, Phase 5 Tempo Tune; §14 for Tempo's own models).

Everything below follows the rules: CPU only, free tiers and open models only, each user's own
keys, and every provider's terms.

**Works today** means: Tempo routes the question to a free model, checks the answer
(heuristics plus a judge from another model family), fixes or merges when it is weak, shows
every stage, and exposes it all as an OpenAI-compatible API (`tempo/auto`), a CLI and a web page.

| # | Scenario | Phase |
|---|---|---|
| 1 | Coding assistant backend | 3 |
| 2 | Pull-request reviewer | 4 |
| 3 | Test writer | 3 |
| 4 | Log explainer | 4 |
| 5 | Research with cross-checked answers | 4 |
| 6 | Private document Q&A | 4 |
| 7 | Long-document summaries | 3 |
| 8 | Support ticket triage | 5 |
| 9 | Messy text to JSON | 3 |
| 10 | Batch jobs | 4 |
| 11 | Safety filter | 4 |
| 12 | Tutor with checked answers | 3 |
| 13 | Indian-language translation | 3 |
| 14 | Offline classroom | 3 |
| 15 | Backend for chat apps and bots | 4 |
| 16 | Model layer for agent frameworks | 4 |
| 17 | MCP second opinion | 4 |
| 18 | Benchmark lab | 3 |
| 19 | Router research | 3 |
| 20 | Decision models on request | 5 |

---

### 1. Coding assistant backend

**Example:** an editor plug-in points its OpenAI base URL at Tempo and asks "Write a Python
function that parses ISO-8601 durations, with tests."
**Today:** routed as a code task; a reply without a code block fails when code was clearly asked
for; Python that doesn't parse fails; a cascade fixes it.
**Still needs:** running the code and its tests in the WebAssembly sandbox (so "passed" means
the tests pass), tool calls passed through the API, conversation memory. **Phase 3** (sandbox),
Phase 4 (tools).

### 2. Pull-request reviewer

**Example:** a CI step sends a diff and asks "Review this change for bugs and missing tests",
getting comments from two model families merged into one review.
**Today:** `mixture` strategy drafts with 2–3 families and merges; `--time-budget` and stage
budgets bound the cost.
**Still needs:** a diff-aware prompt and output format (file, line, comment), a GitHub Action
example, chunking for large diffs (decompose by file). **Phase 4.**

### 3. Test writer

**Example:** "Write pytest tests for this function" with the function pasted in.
**Today:** code-task routing and checks; the judge grades coverage in words.
**Still needs:** running the generated tests against the given code in the sandbox, and a
reward from that for Tempo-Core's GRPO stage (TEMPO_MODELS.md §2). **Phase 3.**

### 4. Log explainer

**Example:** paste 2,000 lines of a failing service's log and ask "What went wrong first, and
why?"
**Today:** long-context routing picks models whose context window fits; summaries are checked.
**Still needs:** trimming and de-duplicating logs before sending (fewer tokens, less free quota),
privacy scrubbing of IPs and emails before a hosted model sees them (`--private` keeps it local
today). **Phase 4** (scrubbing is Phase 3).

### 5. Research with cross-checked answers

**Example:** "What changed in Python 3.13's garbage collector?", answered by two families and
merged, with disagreements called out.
**Today:** `mixture` drafts from different families and a merge that resolves disagreements; the
judge flags unsupported claims.
**Still needs:** web search as a tool with cited sources, and a "claims must agree" check.
**Phase 4.**

### 6. Private document Q&A

**Example:** ask questions about a contract without it leaving the computer.
**Today:** `--private` (or mode `private`) uses only local Ollama models; `--no-logging` avoids
free tiers that may log or train on prompts.
**Still needs:** document upload in the web page, retrieval over long documents (local
embeddings already exist), citing the passage an answer came from. **Phase 4.**

### 7. Long-document summaries

**Example:** a 40-page report summarised in five bullet points (tested in step 1 with a 42 KB
document).
**Today:** summarize tasks go to long-context models; the checker catches cut-off and repeated
text.
**Still needs:** map-reduce summaries for documents longer than any free model's window
(decompose by section), and Tempo-Core trained on checked summaries to do it on CPU.
**Phase 3.**

### 8. Support ticket triage

**Example:** "My payments failed for three days" → team: billing, urgency: high, with
probabilities.
**Today:** any chat model can classify, but it costs a model call per ticket.
**Still needs:** a Tempo Tune decision model (a Laya checkpoint per scenario) that answers typed
questions on CPU in under a second with calibrated probabilities. **Phase 5.**

### 9. Messy text to JSON

**Example:** "Extract name, date and amount from this email as JSON."
**Today:** extract tasks are recognised; an answer without valid JSON is a hard failure and
gets fixed.
**Still needs:** checking against a JSON Schema the caller sends (`response_format`), not just
"is it JSON". **Phase 3.**

### 10. Batch jobs

**Example:** summarise 5,000 product reviews overnight within free limits.
**Today:** `tempo collect` shows the pattern: paced, resumable, within every free limit, keeping
half of each daily quota for people.
**Still needs:** a general `tempo batch` command and API (a JSONL file in, results out), with
the same pacing, resume and quota reserve. **Phase 4.**

### 11. Safety filter

**Example:** check every user message in a chat app for unsafe content before it reaches a
model.
**Today:** safety models are registered by type (Groq's safeguard and prompt-guard models,
NVIDIA's and OpenRouter's content-safety models), but not called.
**Still needs:** a `/v1/moderations`-style endpoint that calls them, and an input/output filter
option in the engine. **Phase 4.**

### 12. Tutor with checked answers

**Example:** a student asks a maths word problem in Hindi and gets a worked answer in Hindi.
**Today:** word problems route as maths; answers mostly in the wrong language fail and are
rewritten; a judge checks the working.
**Still needs:** numeric verification of maths answers (compute the result, not just judge it),
step-by-step hints mode. **Phase 3.**

### 13. Indian-language translation

**Example:** "Translate this notice into Gujarati."
**Today:** translation is recognised, the requested language is checked by script, and models
with better multilingual skills rank higher for non-Latin input.
**Still needs:** measured translation skill per model and language (eval sets for Hindi,
Gujarati, Tamil and others from openly licensed data), and Tempo-Core trained on checked
translations. **Phase 3.**

### 14. Offline classroom

**Example:** a school computer lab with no reliable internet uses Tempo on one CPU machine.
**Today:** local Ollama models, mode `private`, the web page, no GPU.
**Still needs:** Tempo-Core in GGUF as a strong offline default, a one-command installer for
Windows, and a teacher view of usage. **Phase 3** (Tempo-Core), Phase 4 (installer).

### 15. Backend for chat apps and bots

**Example:** a Telegram or WhatsApp bot calls Tempo's OpenAI-compatible API for every message.
**Today:** the API (streaming included), users with their own Tempo keys, bring-your-own-key
provider keys, and quota tracking per user.
**Still needs:** conversation memory, per-user rate limits in the gateway, and shared state
(Redis) so several servers share quota counters. **Phase 4.**

### 16. Model layer for agent frameworks

**Example:** LangChain, LlamaIndex or an agent loop uses `tempo/auto` as its model.
**Today:** OpenAI-compatible chat completions with streaming.
**Still needs:** tool/function calls passed through and checked, JSON-schema outputs, and
routing to models whose `tools` support is known (the catalog already records it). **Phase 4.**

### 17. MCP second opinion

**Example:** a coding agent calls a `second_opinion` MCP tool: "Is this migration safe?", and
Tempo answers with models from other families than the agent's.
**Today:** the engine can already exclude families and mix drafts.
**Still needs:** the MCP server (ARCHITECTURE §6.2) with `ask_tempo`, `second_opinion` and
`list_models` tools. **Phase 4.**

### 18. Benchmark lab

**Example:** "Which free model is best at Gujarati maths this week?"
**Today:** `tempo eval` measures models on a probe set (never with providers whose terms forbid
benchmarking, such as Cohere); `tempo models --free` shows the live catalog and health.
**Still needs:** larger, versioned eval sets per task and language, results over time, and a
page that shows them. **Phase 3.**

### 19. Router research

**Example:** a researcher compares rules, kNN and a two-tower router on Tempo's logged
decisions.
**Today:** every decision is logged with its outcome; `tempo export-laya` and `tempo laya
compare` exist.
**Still needs:** exports in the formats router libraries use (LLMRouter, RouteLLM), exploration
traffic (sometimes a second model answers too), and an A/B switch. **Phase 3.**

### 20. Decision models on request

**Example:** "Build me a model that decides whether a loan document is complete", described in
plain English, trained, and served on CPU.
**Today:** the building blocks: Laya on CPU, typed decisions, exports and the Kaggle notebook
path.
**Still needs:** Tempo Tune itself: scenario to dataset (with "yes" models only), offline
training, the promotion gate, and serving the result as its own model id. **Phase 5.**
