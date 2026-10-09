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
installs **`tempo-server`** as its main command (every doc and the Docker image use it). The
`tempo` alias stays for 0.x with a notice and is removed before 1.0 (owner's decision, step 6).

## 2. No keys in the repository

Already true (checked 2026-09-28 by scanning every tracked file for Groq, OpenAI-style,
Google, NVIDIA and OpenRouter key patterns: nothing found):

- `.env` is in `.gitignore`; `.env.example` has empty values only.
- Tempo never prints, logs or stores a key in plain text: users' keys are encrypted at rest and
  shown only as a one-way fingerprint (`fp:1a2b3c4d`); keys go in headers, never in URLs.
- Tests use placeholder values such as `placeholder-key`.

Before going public:

- [ ] Turn on GitHub's secret scanning and push protection (Settings → Code security).
- [x] Scan the full history once, not just the current files: done 2026-09-29 with gitleaks
      8.28.0 over all 57 commits and a pattern search for every provider's key format; nothing
      found (details in STATUS.md). Rotate any key if a later scan ever finds one.
- [x] Scan again just before switching to public: done 2026-09-30 over every commit on every
      branch (106 after step 11); no keys (details in STATUS.md, "Pre-public check").
- [x] Scan once more on 2026-10-09 (`main` after the tidy, 59 commits, every ref, no unreachable
      objects): key formats for Anthropic, OpenAI, AWS, GitHub, Slack, NVIDIA, Groq, Cerebras,
      Google, Hugging Face and OpenRouter, JWTs, private-key blocks, hard-coded passwords and
      bearer tokens over every added line: nothing (details in STATUS.md, "Pre-public re-check").
- [ ] **owner**: the merge commits made on GitHub (`ec764cc`, `8ac4046`, `266c9c5` and every
      later one until the setting below is on) carry the owner's personal email as their
      author. Once public, anyone can read it. Either accept that, or rewrite `main` before
      switching (changes every later commit id and needs a force push). Either way, turn on
      GitHub → Settings → Emails → "Keep my email addresses private" so later merges use the
      `noreply` address.
- [ ] Turn on private vulnerability reporting (Settings → Code security): SECURITY.md and
      CODE_OF_CONDUCT.md send reporters to that button.
- [ ] Switch: Settings → General → Danger Zone → Change visibility → Make public.
- [x] Keep the rule in CONTRIBUTING.md: no real keys in code, tests, issues or logs (the bug
      report form says it too).

### Going public: the owner's steps, in order

Nothing in the repository blocks the switch; these are GitHub settings a session can't change.

1. **Email** (github.com → your profile → Settings → Emails): "Keep my email addresses private"
   and "Block command line pushes that expose my email". Decide about the commits above first.
2. **Branches** (repository → Settings → General): "Automatically delete head branches" on, and
   delete any branch except `main` (repository → Branches).
3. **Code security** (repository → Settings → Code security): private vulnerability reporting,
   Dependabot alerts, secret scanning and push protection on (the last two are free for public
   repositories; turn them on right after the switch if they aren't offered before it).
4. **Protect `main`** (repository → Settings → Rules → Rulesets → New branch ruleset, target the
   default branch): require a pull request, require status checks (the CI jobs, once they have
   run on a pull request), block force pushes and deletions. This makes CLAUDE.md rule 12 hold
   for everyone, not just sessions.
5. **Switch**: Settings → General → Danger Zone → Change visibility → Make public.
6. **About box** (the gear next to "About" on the repository page): the description "Open models
   plus the system that runs and trains them: routes each question to the best free or open
   model, checks the answer, runs on CPU." and topics `llm`, `llm-router`, `openai-compatible`,
   `litellm`, `mcp`, `free-llm`, `ollama`, `python`.
7. **Check**: the Actions tab runs CI (Windows and macOS now also run on pushes to `main`), the
   README's CI badge turns green, and the one-line installers work from a computer that isn't
   signed in to GitHub.

Already in the repository (2026-10-09): LICENSE, NOTICE, README, CONTRIBUTING.md,
CODE_OF_CONDUCT.md (Contributor Covenant 2.1), SECURITY.md, issue forms (bug, idea, and a link
to the private security form), a pull request checklist, and Dependabot for the workflow actions
(monthly, one grouped pull request).

## 3. The `tempo-server terms` table in the README

The README carries a table of every provider's training verdict and data policy, generated
from `models.yaml` (the same facts `tempo-server terms` prints), with the date the quotes were last
checked. Before each release:

- [ ] `tempo-server terms --check` (every quote still on its provider's page)
- [ ] update the table and its date from `tempo-server terms`

## 4. Examples folder

`examples/` holds short, runnable examples: the OpenAI Python client, curl, streaming with the
thinking window, privacy options, and a bring-your-own-key request. Each runs against a local
`tempo-server serve`, in demo mode (`TEMPO_ENABLE_MOCK=1`) if no key is set, so anyone can try them
without an account.

## 5. A demo

- **Local demo (works today):** `TEMPO_ENABLE_MOCK=1 tempo-server serve`, then open
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
  key vault, auth, the API, the code sandbox, prompt injection between stages), and the
  response time.
- [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md): Contributor Covenant 2.1, with reports going
  to the maintainer through the private reporting form (no email address published).
- `.github/ISSUE_TEMPLATE/` (bug report and idea forms; security goes to the private form)
  and `.github/pull_request_template.md` (the CONTRIBUTING checklist).

## 7. Before the first public release

- [x] Licence: Apache-2.0, `LICENSE` and `NOTICE` added (step 4).
- [x] History scanned (2026-09-29, 2026-09-30 and 2026-10-09).
- [ ] Secret scanning and push protection on (owner, §2).
- [x] README: the one-line description, quick start, the terms table, links to USE_CASES.md and
      TEMPO_MODELS.md, and badges (CI, licence, Python, systems) (2026-10-09).
- [ ] `tempo-server terms --check` clean; STATUS.md current.
- [x] Tests and lint green on a clean checkout (`pip install -e ".[dev]" && pytest -q`): 482
      passed, 51 skipped (the sandbox and browser extras, which CI runs), 2026-10-09.
- [ ] Tag `v0.x` and write release notes (what works, what needs keys, what is planned).
- [ ] Docker image: publishing the GitHub release runs `.github/workflows/docker-publish.yml`
      (tests first, then `ghcr.io/<owner>/tempo-server:<version>` and `latest`, amd64 and
      arm64). Then make the package public under the repository's Packages settings.
- [ ] PyPI with Trusted Publishing (no token): on pypi.org, Account → Publishing → add a pending
      publisher (project `tempo-server`, owner `narendrachampaneri`, repository `Tempo-server`,
      workflow `pypi-publish.yml`, environment `pypi`); the same on test.pypi.org with
      environment `testpypi`; create both environments in GitHub (Settings → Environments). A
      manual run of "Publish to PyPI" tries TestPyPI; publishing the `v0.1.0` release publishes
      to PyPI (the tag must match the version).
- [ ] GitHub Pages: Settings → Pages → Deploy from branch `main`, folder `/docs`. The landing
      page is `docs/index.html`, the demo `docs/demo/`.
- [ ] Make `main` the default branch: the installers and README links point at it.
- [ ] Models, when trained: model cards and licences (TEMPO_MODELS.md §5), promotion-gate
      results attached.
