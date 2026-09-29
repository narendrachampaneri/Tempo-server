# Examples

Each example talks to a local Tempo server. With no provider keys, start it in demo mode, so the
examples run with no account and no network:

```bash
TEMPO_ENABLE_MOCK=1 tempo-server serve      # http://127.0.0.1:8000
pip install openai                   # for the Python examples
```

| File | Shows |
|---|---|
| `openai_client.py` | Tempo as a model in the official OpenAI Python client, with conditions |
| `stream_trace.py` | Streaming the answer and the thinking-window events |
| `tools_and_json.py` | Tool calls (with the tool result sent back), strict JSON schema, and an image |
| `privacy.py` | `local_only` and `no_logging` privacy options |
| `curl.sh` | The API with curl: a question and the model list |
| `mcp_config.json` | Tempo-server as an MCP server for Claude Desktop, Cursor and others ([docs/MCP.md](../docs/MCP.md)) |
| `sdk_python.py` | The Python SDK (`sdk/python`): the thinking window, an answer, feedback, the quota |
| `sdk_js.mjs` | The JavaScript SDK (`sdk/js`): the same in Node |

With real keys (see `.env.example`), the same examples use free models instead of the demo ones.
