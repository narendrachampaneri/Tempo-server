# Tempo-server: steps 1 to 6

Pull request from `claude/multi-model-ai-platform-o0fx9v` into `main`. Everything was built
under the owner's rules ([CLAUDE.md](../CLAUDE.md)): CPU only, free tiers and open models only,
live model lists, each user's own keys, every provider's terms respected. Full detail per step:
[docs/STATUS.md](STATUS.md).

## Step 1: setup and rules
- CLAUDE.md with the owner's ten rules; full test suite and a mock run (CLI and web: greeting,
  code, maths, long document, Gujarati/Hindi, stage events, fallback after a limit error).
- A live, public-only sync of OpenRouter's free models.

## Step 2: live free-model catalog and new providers
- OpenRouter endpoint health (every 15 minutes; degraded and failing endpoints ranked down).
- Model types (chat-capable only); providers Cloudflare, NVIDIA (owner only), Cohere (user's own
  trial key, answers only), Mistral, OpenCode Zen (off, user's own key); GitHub Models retired.
- `tempo-server models --free`, data-policy flags, Ollama licences, `collect --yes-only`.

## Step 3: Tempo's own models (plan and data only)
- `export-sft` and `export-pairs` ("yes" rows only, licence and source on each row), the training
  plan, promotion gate, collapse protection, 20 use cases, the publishing checklist.

## Step 4: OpenAI API compatibility
- Tool calls on every provider (native or emulated, validated), strict JSON schema, images,
  streaming; tested with the real OpenAI Python SDK.

## Step 5: easy for other people
- `tempo-server setup` wizard, `tempo-server quota` and the web quota strip, fallback to local
  models when every free quota is used up, local first for simple questions.
- Packaging (wheel, Dockerfile, GHCR workflow), docs/CONNECT.md for seven apps, the recorded
  demo page, README quick start. Owner's step-4 decisions (structured outputs, tool follow-ups,
  502/503, consent switch, oasst2, SSN/IBAN scrubbing).

## Step 6: works on Windows, macOS and Linux, installs like a professional tool
- CI: Linux (Python 3.11 to 3.14) on every push; Windows (3.11 to 3.14) and macOS (3.13) on pull
  requests and releases; install tests on all three systems (install.sh / install.ps1, pipx and
  uv, then `--version`, non-interactive setup, a demo question and `doctor`).
- Fixes: data folder per system (with a safe one-time move from `~/.tempo`), UTF-8 file I/O
  everywhere (enforced by ruff), UTF-8 console output on Windows, `tzdata` on Windows, LF line
  endings, POSIX-only permission checks, tests isolated from the real home folder.
- One-line installers (`install.sh`, `install.ps1`), README install per system, developer setup
  moved to CONTRIBUTING.md, `tempo-server doctor`, PyPI Trusted Publishing workflow (no token).
- Owner's step-5 decisions: `.env` keys owner-only (`TEMPO_SHARE_SERVER_KEYS=1` to share), one
  quota bucket per key, `tempo` alias with a removal notice, local first only with a running
  Ollama (and a note in the thinking window), a GitHub Pages landing page (`docs/index.html`).

Nothing is published: PyPI, the Docker image and GitHub Pages wait for the owner.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01UisHQmMVDstvmrNwgrPMxT
