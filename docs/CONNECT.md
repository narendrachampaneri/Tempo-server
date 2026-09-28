# Connect an app to Tempo-server

_Step 5, 2026-09-28._ Tempo-server speaks the OpenAI Chat Completions API, so any app that lets
you set an "OpenAI-compatible" base URL can use it. Every app below needs the same three values:

| Setting | Value |
|---|---|
| Base URL | `http://localhost:8000/v1` (from inside Docker: `http://host.docker.internal:8000/v1`) |
| Model | `tempo/auto` (also `tempo/fast`, `tempo/best`, `tempo/private`, or any id from `tempo-server models`) |
| API key | your **Tempo** key (below), never a provider key |

**Which API key?** Tempo adds your provider keys itself (the ones `tempo-server setup` stored),
so the app only needs to prove it may use Tempo:

- **Just you, on your own computer** (no `TEMPO_API_KEY`, no users): any placeholder works, for
  example `local`.
- **You set `TEMPO_API_KEY`** (for example in Docker): use that value.
- **Other people use your server:** give each one a key with `tempo-server users add <name>`
  (shown once). Each person adds their own free provider keys on the web page's Keys tab; your
  provider keys are only for you.

Start the server first: `tempo-server serve` (or the Docker image, see the README). Tool calls,
strict JSON (`response_format`), images and streaming all work
([ARCHITECTURE.md](./ARCHITECTURE.md), step 4).

The whole flow is tested with the official OpenAI Python SDK against a real server
(`tests/test_connect.py`): listing models, a chat answer, streaming with a user's Tempo key, and
a wrong key refused.

The settings below follow each app's own documentation as of 2026-09-28. Apps change their
settings screens often: if a field has moved, look for "OpenAI-compatible", "custom endpoint" or
"base URL".

---

## Open WebUI

**Admin Panel → Settings → Connections → OpenAI API → +** (add a connection):

- URL: `http://localhost:8000/v1` (Open WebUI in Docker: `http://host.docker.internal:8000/v1`)
- Key: your Tempo key
- Save. `tempo/auto`, `tempo/fast`, `tempo/best` and every ready model appear in the model menu.

Or when starting Open WebUI:

```bash
docker run -d -p 3000:8080 \
  -e OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1 \
  -e OPENAI_API_KEY=local \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data ghcr.io/open-webui/open-webui:main
```

Docs: https://docs.openwebui.com/getting-started/quick-start/starting-with-openai-compatible

## LibreChat

Add a custom endpoint to `librechat.yaml`, and `TEMPO_KEY=<your Tempo key>` to LibreChat's
`.env`:

```yaml
endpoints:
  custom:
    - name: "Tempo"
      apiKey: "${TEMPO_KEY}"
      baseURL: "http://host.docker.internal:8000/v1"
      models:
        default: ["tempo/auto", "tempo/fast", "tempo/best"]
        fetch: true
      titleConvo: true
      titleModel: "tempo/fast"
      modelDisplayLabel: "Tempo"
```

Docs: https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint

## Continue (VS Code and JetBrains)

In `~/.continue/config.yaml` (or the config opened from Continue's settings):

```yaml
name: Tempo
version: 0.0.1
schema: v1
models:
  - name: Tempo (auto)
    provider: openai
    model: tempo/auto
    apiBase: http://localhost:8000/v1
    apiKey: local
    roles: [chat, edit, apply]
  - name: Tempo (fast)
    provider: openai
    model: tempo/fast
    apiBase: http://localhost:8000/v1
    apiKey: local
    roles: [autocomplete]
```

Docs: https://docs.continue.dev/customize/model-providers/top-level/openai

## Aider

```bash
export OPENAI_API_BASE=http://localhost:8000/v1
export OPENAI_API_KEY=local          # your Tempo key
aider --model openai/tempo/auto --no-show-model-warnings
```

The `openai/` prefix tells Aider to use the OpenAI-compatible API; `--no-show-model-warnings`
hides the notice that Aider doesn't know `tempo/auto`'s context size.

Docs: https://aider.chat/docs/llms/openai-compat.html

## OpenCode

In `opencode.json` (project) or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "tempo": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Tempo-server",
      "options": {
        "baseURL": "http://localhost:8000/v1",
        "apiKey": "{env:TEMPO_KEY}"
      },
      "models": {
        "tempo/auto": { "name": "Tempo (auto)" },
        "tempo/best": { "name": "Tempo (best)" }
      }
    }
  }
}
```

Then `export TEMPO_KEY=local` and pick "Tempo (auto)" with `/models`.

Docs: https://opencode.ai/docs/providers/#custom-provider

## n8n

1. **Credentials → New → OpenAI API**: API Key = your Tempo key; Base URL =
   `http://localhost:8000/v1` (n8n in Docker: `http://host.docker.internal:8000/v1`).
2. In a workflow, add an **OpenAI Chat Model** node (for the AI Agent or Basic LLM Chain) with
   that credential, and set the model **By ID** to `tempo/auto`.

Docs: https://docs.n8n.io/integrations/builtin/credentials/openai/

## LangChain

Python (`pip install langchain-openai`):

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(base_url="http://localhost:8000/v1", api_key="local", model="tempo/auto")
print(llm.invoke("Explain TCP vs UDP in two lines").content)
```

JavaScript (`npm install @langchain/openai`):

```js
import { ChatOpenAI } from "@langchain/openai";

const llm = new ChatOpenAI({
  model: "tempo/auto",
  apiKey: "local",
  configuration: { baseURL: "http://localhost:8000/v1" },
});
console.log((await llm.invoke("Explain TCP vs UDP in two lines")).content);
```

Tool calling (`llm.bind_tools([...])`) and structured output (`llm.with_structured_output(...)`)
use Tempo's tool calls and strict JSON.

Docs: https://python.langchain.com/docs/integrations/chat/openai/

## The OpenAI SDK (any other app)

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="local")
reply = client.chat.completions.create(
    model="tempo/auto", messages=[{"role": "user", "content": "Hello!"}]
)
print(reply.choices[0].message.content)
```

More in [`examples/`](../examples/).

## If it doesn't work

| Symptom | Fix |
|---|---|
| Connection refused from an app in Docker | Use `host.docker.internal` instead of `localhost` (Linux: add `--add-host=host.docker.internal:host-gateway`), or run Tempo with `serve --host 0.0.0.0` |
| 401 Invalid or missing API key | Users exist or `TEMPO_API_KEY` is set: use a Tempo key, not a placeholder |
| 503 with Retry-After | No model is ready, or every free quota is used up: `tempo-server quota`, add a key with `tempo-server setup`, or start Ollama |
| 502 | Models answered, but none gave valid JSON or a valid tool call; the body says why |
| The app lists no models | It may not call `/v1/models`: type `tempo/auto` by hand |
