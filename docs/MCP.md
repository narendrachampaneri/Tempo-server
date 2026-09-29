# Use Tempo-server from any AI assistant (MCP)

_Step 7, 2026-09-29._ Tempo-server is an [MCP](https://modelcontextprotocol.io) server, so
Claude Desktop, Claude Code, Cursor, VS Code and any other MCP client can call it. It is built on
the official MCP Python SDK and comes with the normal install (`pipx install tempo-server`, the
one-line installers, or `uv tool install tempo-server`).

## The tools

| Tool | What it does | Returns |
|---|---|---|
| `ask` | Answers a question the Tempo way: best free or open model, checked by a judge from another family, fixed when weak. Options: `mode` (`auto`, `fast`, `best`), `private` (never a model whose free tier may log or train on prompts), `local_only` (only local Ollama models) | the answer, the model, every model used, the check results, stages |
| `second_opinion` | Asks two different model families; a third (when available) lists where they agree and differ | both answers with their models, and the comparison (`agree` / `partly` / `disagree`, agreements, differences, summary) |
| `verify` | Checks an answer you already have: Tempo's quick checks plus a judge from a different family than `answer_model` | `pass` / `fail`, a score out of 10, the problems found, the judge |
| `models` | The live free-model list with limits, data policy and health | one row per model, with `ready` for you |
| `quota` | Free requests left today per provider | per provider, and whether everything is used up |

**Privacy:** assistants often send private code or documents with a question, so questions that
arrive over MCP are logged apart (tagged `mcp`) and kept out of every training export
(`export-sft`, `export-pairs`, `export-laya`) unless you set `TEMPO_TRAIN_ON_MCP=1`. Use
`private: true` to keep them away from free tiers that may log or train on prompts, and
`local_only: true` to keep them on your computer.

Every call goes through the same engine as the web app and API: free-quota limits, fallbacks,
health checks and privacy options all apply. The tools cost free requests like any question
(`quota` and `models` cost none).

## Two ways to connect

- **stdio (desktop apps, recommended):** the app starts `tempo-server mcp` itself. It runs as you,
  with the keys `tempo-server setup` stored and your local models. No key needed in the config.
- **HTTP:** `tempo-server mcp --http` serves `http://127.0.0.1:8001/mcp`. Every request needs your
  **Tempo** key: `Authorization: Bearer <key>` (from `tempo-server users add <name>`, or
  `TEMPO_API_KEY`). Each user's own provider keys are used, never someone else's.

Run `tempo-server setup` first (or have Ollama running), so there is a model to answer. Then
`tempo-server doctor` checks everything.

**Where is `tempo-server`?** Desktop apps often don't see your terminal's `PATH`, so use the full
path in `"command"`. Find it with `which tempo-server` (macOS, Linux) or `where.exe tempo-server`
(Windows). With the one-line installer or `uv tool install` it is usually:

| System | Full path |
|---|---|
| macOS | `/Users/<you>/.local/bin/tempo-server` |
| Linux | `/home/<you>/.local/bin/tempo-server` |
| Windows | `C:\Users\<you>\.local\bin\tempo-server.exe` (in JSON: `C:\\Users\\<you>\\.local\\bin\\tempo-server.exe`) |

With `pipx` it is the folder `pipx environment --value PIPX_BIN_DIR` prints.

Never put a provider key in these config files. Over stdio, Tempo-server reads your keys from its
own encrypted vault; over HTTP, only your Tempo key goes in a header.

---

## Claude Desktop

Settings → Developer → Edit Config opens `claude_desktop_config.json`:

| System | File |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | Claude Desktop has no official Linux build; use Claude Code or another client below |

macOS:

```json
{
  "mcpServers": {
    "tempo-server": {
      "command": "/Users/you/.local/bin/tempo-server",
      "args": ["mcp"]
    }
  }
}
```

Windows:

```json
{
  "mcpServers": {
    "tempo-server": {
      "command": "C:\\Users\\you\\.local\\bin\\tempo-server.exe",
      "args": ["mcp"]
    }
  }
}
```

Replace `you` with your user name, save, and restart Claude Desktop. The tools appear under the
tools (🔨) menu. Try: "Use tempo-server's second_opinion: is it safe to store passwords with SHA-256?"

## Claude Code

The same command on macOS, Linux and Windows (PowerShell), for all your projects:

```bash
claude mcp add --scope user tempo-server -- tempo-server mcp
```

Over HTTP (start `tempo-server mcp --http` first; your Tempo key in `TEMPO_KEY`):

```bash
claude mcp add --scope user --transport http tempo-server http://127.0.0.1:8001/mcp --header "Authorization: Bearer $TEMPO_KEY"
```

On Windows PowerShell write `$env:TEMPO_KEY` instead of `$TEMPO_KEY`. Check with `claude mcp list`
or `/mcp` inside Claude Code. Or put it in the project's `.mcp.json` (shared with your team;
no keys in it):

```json
{
  "mcpServers": {
    "tempo-server": {
      "command": "tempo-server",
      "args": ["mcp"]
    }
  }
}
```

## Cursor

Settings → MCP → Add new global MCP server opens `mcp.json`:

| System | Global file | Project file |
|---|---|---|
| macOS, Linux | `~/.cursor/mcp.json` | `.cursor/mcp.json` |
| Windows | `%USERPROFILE%\.cursor\mcp.json` | `.cursor\mcp.json` |

macOS and Linux (use your full path from the table above):

```json
{
  "mcpServers": {
    "tempo-server": {
      "command": "/home/you/.local/bin/tempo-server",
      "args": ["mcp"]
    }
  }
}
```

Windows:

```json
{
  "mcpServers": {
    "tempo-server": {
      "command": "C:\\Users\\you\\.local\\bin\\tempo-server.exe",
      "args": ["mcp"]
    }
  }
}
```

Over HTTP (the key comes from your environment, not the file):

```json
{
  "mcpServers": {
    "tempo-server": {
      "url": "http://127.0.0.1:8001/mcp",
      "headers": { "Authorization": "Bearer ${env:TEMPO_KEY}" }
    }
  }
}
```

## VS Code (GitHub Copilot agent mode)

Command Palette → "MCP: Open User Configuration" (all workspaces), or create `.vscode/mcp.json`
in a project. The file is the same on every system; only the path differs.

macOS and Linux:

```json
{
  "servers": {
    "tempo-server": {
      "type": "stdio",
      "command": "/home/you/.local/bin/tempo-server",
      "args": ["mcp"]
    }
  }
}
```

Windows:

```json
{
  "servers": {
    "tempo-server": {
      "type": "stdio",
      "command": "C:\\Users\\you\\.local\\bin\\tempo-server.exe",
      "args": ["mcp"]
    }
  }
}
```

Over HTTP, VS Code asks for your Tempo key once and stores it securely:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "tempo-key",
      "description": "Tempo-server API key",
      "password": true
    }
  ],
  "servers": {
    "tempo-server": {
      "type": "http",
      "url": "http://127.0.0.1:8001/mcp",
      "headers": { "Authorization": "Bearer ${input:tempo-key}" }
    }
  }
}
```

Open the Chat view in Agent mode and pick the Tempo-server tools from the tools button.

## Any other MCP client

- stdio: command `tempo-server`, arguments `["mcp"]`.
- HTTP: `http://127.0.0.1:8001/mcp` (Streamable HTTP), header `Authorization: Bearer <Tempo key>`.
  `tempo-server mcp --http --host 0.0.0.0 --port 8001` listens on your network; put it behind HTTPS
  before exposing it beyond your own computer.

Once Tempo-server is on PyPI, clients that run `uvx` can skip the install: command `uvx`,
arguments `["tempo-server", "mcp"]`.

## If it doesn't work

| Symptom | Fix |
|---|---|
| The app says the server failed to start | Use the full path to `tempo-server` (see above); run `tempo-server mcp` in a terminal to see the error (it waits for input: Ctrl-C to quit) |
| Tools answer "No model available" | Run `tempo-server setup` to add a free key, or start Ollama; `tempo-server doctor` shows what is missing |
| HTTP: 401 | Send your Tempo key: `Authorization: Bearer <key>` (`tempo-server users add <name>` makes one) |
| `local_only` fails | No local model: install Ollama and `ollama pull qwen3:1.7b`, then `tempo-server setup --only ollama` |

The configuration formats above follow each app's documentation as checked on 2026-09-29:
[Claude Desktop](https://modelcontextprotocol.io/quickstart/user),
[Claude Code](https://docs.anthropic.com/en/docs/claude-code/mcp),
[Cursor](https://docs.cursor.com/context/model-context-protocol),
[VS Code](https://code.visualstudio.com/docs/copilot/chat/mcp-servers). Apps change their
settings screens often; the `command` and `args` stay the same.
