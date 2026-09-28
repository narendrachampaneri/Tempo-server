# Publishing Tempo-server

_Step 3, 2026-09-28. A checklist for making the repository and Tempo's models public. The owner
decides the items marked **owner**._

## What Tempo is, in one line

> **Tempo is open models plus the system that runs and trains them**: it routes every question
> to the best free or open model, checks the answer, fixes it when it is weak, shows every step,
> and trains its own small open models (Tempo-Router, Tempo-Judge, Tempo-Core) from the answers
> that passed, all on an ordinary CPU.

Use this line (or a shorter form of it) in the README's first paragraph, the GitHub "About"
box, and every model card.

## 1. Licence (owner)

| Option | For | Against |
|---|---|---|
| **Apache-2.0** | Matches Laya (Apache-2.0), which Tempo-Router and Tempo-Judge build on; an explicit patent grant; common for ML projects | Longer; changed files must carry a notice |
| **MIT** | Short and simple; matches MIT base models such as Phi-4-mini | No explicit patent grant |

**Chosen: Apache-2.0** (owner, step 4). `LICENSE` carries "Copyright 2026 The Tempo-server
authors", `NOTICE` credits Laya, and `pyproject.toml` declares Apache-2.0. Also:

- add a `LICENSE` file with the chosen text and the copyright holder's name;
- models inherit their base model's licence (TEMPO_MODELS.md §5), which each model card states;
- datasets are published only where every row's licence allows it; CC-BY-SA rows (Dolly) make
  a published dataset share-alike.

Dataset credits (for example MBPP's CC-BY-4.0 attribution) go in the model cards.

## Name

The public name is **Tempo-server**: the README, `LICENSE`, the package metadata
(`tempo-server` on PyPI), the Docker image (`tempo-server`) and the Hugging Face placeholders
(`<owner>/tempo-server-*`).

Checked 2026-09-28 (step 5):

| Where | Result |
|---|---|
| PyPI `tempo-server`, `tempo_server`, `temposerver` | free (404) |
| PyPI `tempo` | taken by an unrelated project |
| Hugging Face models and datasets named `tempo-server`; user/org `tempo-server` | free |
| Hugging Face Spaces | one unrelated Space, `kokluch/tempo-edf-mcp-server` |
| Grafana Tempo | its Makefile builds `tempo`, `tempo-query`, `tempo-cli` and `tempo-vulture`; the `grafana/tempo` image has over 100M pulls |

So a `tempo` command can clash with Grafana Tempo on a machine that has both. Tempo-server
installs **`tempo-server`** as its main command (the docs and the Docker image use it) and keeps
`tempo` as a short alias. Suggested: drop the alias before 1.0 if clashes are reported (owner).

## 2. No keys in the repository

Already true (checked 2026-09-28 by scanning every tracked file for Groq, OpenAI-style,
Google, NVIDIA and OpenRouter key patterns: nothing found):

- `.env` is in `.gitignore`; `.env.example` has empty values only.
- Tempo never prints, logs or stores a key in plain text: users' keys are encrypted at rest and
  shown only as a one-way fingerprint (`fp:1a2b3c4d`); keys go in headers, never in URLs.
- Tests use placeholder values such as `placeholder-key`.

Before going public:

- [ ] Turn on GitHub's secret scanning and push protection (Settings → Code security).
- [ ] Scan the full history once, not just the current files (for example with `gitleaks
      detect` or GitHub's secret scanning report), and rotate any key ever committed.
- [ ] Keep the rule in CONTRIBUTING.md: no real keys in code, tests, issues or logs.

## 3. The `tempo terms` table in the README

The README carries a table of every provider's training verdict and data policy, generated
from `models.yaml` (the same facts `tempo terms` prints), with the date the quotes were last
checked. Before each release:

- [ ] `tempo terms --check` (every quote still on its provider's page)
- [ ] update the table and its date from `tempo terms`

## 4. Examples folder

`examples/` holds short, runnable examples: the OpenAI Python client, curl, streaming with the
thinking window, privacy options, and a bring-your-own-key request. Each runs against a local
`tempo serve`, in demo mode (`TEMPO_ENABLE_MOCK=1`) if no key is set, so anyone can try them
without an account.

## 5. A demo

- **Local demo (works today):** `TEMPO_ENABLE_MOCK=1 tempo serve`, then open
  http://127.0.0.1:8000. The demo models show every stage, a rate-limit fallback, a fix, and
  the language check, with no keys and no network.
- **Public demo (ready):** `docs/demo/index.html` replays `docs/demo/recording.js`, made by
  `tempo-server record-demo`. It is recorded in demo mode now and says so on the page;
  re-record with real models (`tempo-server record-demo` with keys set) before announcing it.
  With GitHub Pages serving `/docs` from `main`, it is at
  `https://<owner>.github.io/Tempo-server/demo/`.
- **Why static:** a hosted demo that runs Tempo needs a server. Hugging Face Spaces' free CPU
  Basic hardware (2 vCPU, 16 GB) now needs a paid plan for Spaces that run code (Gradio or
  Docker); only static Spaces are free (huggingface.co/docs/hub/spaces-overview, checked
  2026-09-28). So the free public demo is **a static page**: a recorded run of the thinking
  window (events captured from demo mode, replayed in the browser), plus a short screen
  recording. A live hosted demo waits for a free CPU host whose terms allow it (owner).
- A demo never uses NVIDIA (owner-only, refused in demo mode) or anyone's real keys.

## 6. CONTRIBUTING and SECURITY

- [CONTRIBUTING.md](../CONTRIBUTING.md): the project's rules (CLAUDE.md, in contributor
  language), setup, tests and lint, small commits, docs with every change, how to add a
  provider (live list, limits with source and date, terms quote), and what is never accepted
  (keyless tricks, working around rate limits, pooled accounts, GPU-only features).
- [SECURITY.md](../SECURITY.md): how to report a vulnerability privately, what is in scope (the
  key vault, auth, the API, the sandbox once built, prompt injection between stages), and the
  response time.

## 7. Before the first public release

- [x] Licence: Apache-2.0, `LICENSE` and `NOTICE` added (step 4).
- [ ] Secret scanning on; history scanned.
- [ ] README: the one-line description, quick start, the terms table, links to USE_CASES.md and
      TEMPO_MODELS.md.
- [ ] `tempo terms --check` clean; STATUS.md current.
- [ ] Tests and lint green on a clean checkout (`pip install -e ".[dev]" && pytest -q`).
- [ ] Tag `v0.x` and write release notes (what works, what needs keys, what is planned).
- [ ] Docker image: publishing the GitHub release runs `.github/workflows/docker-publish.yml`
      (tests first, then `ghcr.io/<owner>/tempo-server:<version>` and `latest`, amd64 and
      arm64). Then make the package public under the repository's Packages settings.
- [ ] PyPI: `python -m build && twine upload dist/*` from a clean checkout (or add a trusted
      publishing workflow). Until then the README installs from GitHub.
- [ ] GitHub Pages: Settings → Pages → Deploy from branch `main`, folder `/docs`.
- [ ] Models, when trained: model cards and licences (TEMPO_MODELS.md §5), promotion-gate
      results attached.
