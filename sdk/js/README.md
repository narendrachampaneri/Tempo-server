# tempo-server-client (JavaScript / TypeScript)

A thin client for [Tempo-server](https://github.com/narendrachampaneri/Tempo-server)'s HTTP API,
for Node 18+ and browsers: stream the thinking window live, ask for a checked answer, and use the
models, quota, consent and feedback endpoints. Uses `fetch`; no dependencies, no server code;
types included.

```bash
npm install tempo-server-client      # not published yet: npm install ./sdk/js from a clone
```

```ts
import { TempoClient } from "tempo-server-client";

const tempo = new TempoClient({ baseUrl: "http://127.0.0.1:8000", apiKey: "your-tempo-key" });
// In Node, baseUrl and apiKey default to $TEMPO_URL and $TEMPO_API_KEY.

for await (const event of tempo.stream({ prompt: "Explain TCP vs UDP", mode: "best" })) {
  if (event.text) console.log("▸", event.text);                              // the thinking window
  else if (event.type === "answer_delta") process.stdout.write(String(event.delta)); // the answer
}

const answer = await tempo.ask({ prompt: "What is 17% of 2,340?", privacy: "no_logging" });
console.log(answer.text, answer.model, answer.stopReason);
await tempo.feedback(answer.questionId!, "up");

console.log(await tempo.quota());   // free requests left today, per provider
console.log(await tempo.models());  // providers and models, ready or not and why
await tempo.setConsent(true);       // let your questions be used as training data (off by default)
await tempo.deleteMyData();         // delete every question you asked
```

| Method | Endpoint |
|---|---|
| `stream(prompt \| {prompt, messages, mode, privacy, model, max_stages, time_budget_s, quota_budget, strategy, allow_providers, signal})` | `POST /api/ask` (server-sent events), an async iterator of events |
| `ask(...)` | the same, folded into an `Answer` (`text`, `model`, `questionId`, `stages`, `stopReason`, `score`, `events`, `trace`) |
| `feedback(questionId, "up" \| "down" \| 1 \| -1, comment?)` | `POST /api/feedback` |
| `models()`, `quota()`, `me()`, `health()` | `GET /api/models`, `/api/quota`, `/api/me`, `/health` |
| `consent()`, `setConsent(bool)`, `deleteMyData()` | `GET`/`PUT /api/consent`, `DELETE /api/data` |

Errors reject with `TempoError` (`status`, `code`, `message`, `retryAfter` in seconds when every
free quota is used up). Pass `signal` (an `AbortSignal`) to stop a question. The API key is your
**Tempo** key (from `tempo-server users add`), never a provider key; don't ship it in a public web
page. A server that runs just for you needs none. In a browser, a page on another website can call
Tempo-server only if the server's owner lists that website in `TEMPO_CORS_ORIGINS` (off by
default; exact origins, no wildcard).

For the OpenAI-compatible endpoint (`/v1`), use the official `openai` package instead. Apache-2.0.

**Develop:** `npm install && npm test` (builds, then runs the tests against a demo-mode
Tempo-server it starts with `$TEMPO_PYTHON -m tempo.cli serve`, or the one at `$TEMPO_URL`).
