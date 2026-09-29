# tempo-server-client (Python)

A thin client for [Tempo-server](https://github.com/narendrachampaneri/Tempo-server)'s HTTP API:
stream the thinking window live, ask for a checked answer, and use the models, quota, consent and
feedback endpoints. No server code inside; its only dependency is `httpx`. Python 3.9+.

```bash
pip install tempo-server-client      # not published yet: pip install ./sdk/python from a clone
```

```python
from tempo_server_client import TempoClient

tempo = TempoClient(
    "http://127.0.0.1:8000", api_key="your-tempo-key"
)  # or $TEMPO_URL / $TEMPO_API_KEY

for event in tempo.stream("Explain TCP vs UDP", mode="best"):
    if event.text:  # one line per step: stages, models, checks, fallbacks
        print("▸", event.text)
    elif event.type == "answer_delta":  # the answer as it is written
        print(event["delta"], end="")

answer = tempo.ask("What is 17% of 2,340?", privacy="no_logging")
print(answer.text, answer.model, answer.stop_reason)
tempo.feedback(answer.question_id, "up")

print(tempo.quota())  # free requests left today, per provider
print(tempo.models())  # providers and models, ready or not and why
tempo.set_consent(True)  # let your questions be used as training data (off by default)
tempo.delete_my_data()  # delete every question you asked
```

`AsyncTempoClient` has the same methods for asyncio (`async for event in tempo.stream(...)`).

| Method | Endpoint |
|---|---|
| `stream(prompt, messages=, mode=, privacy=, model=, max_stages=, time_budget_s=, quota_budget=, strategy=, allow_providers=)` | `POST /api/ask` (server-sent events) |
| `ask(...)` | the same, folded into an `Answer` (`text`, `model`, `question_id`, `stages`, `stop_reason`, `score`, `events`, `trace`) |
| `feedback(question_id, "up" \| "down" \| 1 \| -1, comment=)` | `POST /api/feedback` |
| `models()`, `quota()`, `me()`, `health()` | `GET /api/models`, `/api/quota`, `/api/me`, `/health` |
| `consent()`, `set_consent(bool)`, `delete_my_data()` | `GET`/`PUT /api/consent`, `DELETE /api/data` |

Errors raise `TempoError` with `status`, `code`, `message` and `retry_after` (seconds, when every
free quota is used up). The API key is your **Tempo** key (from `tempo-server users add`), never a
provider key; a server that runs just for you needs none.

For the OpenAI-compatible endpoint (`/v1`), use the official `openai` package instead
([docs/CONNECT.md](../../docs/CONNECT.md)). Apache-2.0.
