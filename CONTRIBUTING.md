# Contributing to Tempo

Thank you for helping. Tempo is open models plus the system that runs and trains them, built
under a few firm rules. Please read them first; a change that breaks one is not accepted.

## The rules

1. **Software only.** Everything must run on an ordinary CPU computer or a free cloud service.
   No feature may need a GPU at run time; free notebooks (such as Kaggle) are for offline
   training only.
2. **Free and open.** Only free tiers and free or open models. No paid API may be required.
3. **Live facts.** Read model lists and limits from each provider. A hand-entered fact (a limit,
   a licence, a terms verdict) needs a source link and the date it was checked.
4. **Keys.** Never put a real key in code, tests, issues, logs or screenshots. Use placeholder
   values in tests. Tempo shows a stored key only as a fingerprint.
5. **Provider terms.** Never work around a rate limit, pool accounts, or call a service without
   the user's own key where one is required. Each user brings their own keys.
6. **Training data.** Only from sources marked "yes" (`tempo-server terms`), with the licence and
   source on every row.

## Setup (developers only)

Users install Tempo-server as a tool (`pipx`, `uv tool` or the one-line installers in the
README) and never need a virtual environment. To work on Tempo-server itself:

**macOS and Linux**

```bash
git clone https://github.com/narendrachampaneri/Tempo-server && cd Tempo-server
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"                   # editable: your changes apply without reinstalling
pytest -q                                 # the full suite runs offline
ruff check . && ruff format --check .     # lint
TEMPO_ENABLE_MOCK=1 tempo-server serve    # demo models, no keys needed
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/narendrachampaneri/Tempo-server; cd Tempo-server
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -q
ruff check .; ruff format --check .
$env:TEMPO_ENABLE_MOCK = "1"; tempo-server serve
```

Or with uv: `uv venv && uv pip install -e ".[dev]"`. Optional extras for development:
`.[embeddings]`, `.[laya]`, `.[terms]`. Keys can go in `.env` (copy `.env.example`) instead of the
vault.

**Test the installed package the way users get it:**

```bash
python -m build                                   # dist/*.whl and dist/*.tar.gz
pipx install --force dist/*.whl                   # or: uv tool install --force dist/*.whl
TEMPO_SERVER_SOURCE=$PWD/dist/<wheel> sh install.sh   # the one-line installer with a local wheel
```

## Every system

CI runs the tests on Linux (Python 3.11 to 3.14) on every push, and on Windows (3.11 to 3.14) and
macOS (3.13) for pull requests and releases, plus an install test with `install.sh` /
`install.ps1`, `pipx` and `uv` on all three. Keep code portable:

- read and write text files with `encoding="utf-8"` (ruff's `PLW1514` checks most of them);
- build paths with `pathlib`, never with `/` in strings; the data folder comes from
  `tempo/paths.py`;
- file permissions (`chmod`) only matter on POSIX: guard tests with `os.name == "posix"`;
- tests never touch the real home folder (`tests/conftest.py` gives each test its own).

## Making a change

- Small commits, each with tests; tests and lint green before you push.
- Update the docs in the same change (README, docs/ARCHITECTURE.md, docs/STATUS.md where it
  applies).
- New behaviour gets a test; a bug fix gets a test that failed before the fix.
- Tests must not touch the network: use `httpx.MockTransport`, the mock models, or the local
  fake server in `tests/test_litellm_integration.py`.

## Adding a provider

1. A provider entry in `tempo/models.yaml`: key variable, OpenAI-compatible base if it has one,
   signup link, limits with `limits_source` and `limits_checked`, data policy with the sentence
   and link, and `training_on_outputs` with the exact quoted sentences, link and date.
2. A parser for its live model list in `tempo/sync.py` (types, free-only filter).
3. Tests with a mocked list (see `tests/test_providers_catalog.py`).
4. `tempo-server terms --check` must find every quote.

## Reporting problems

Bugs and ideas: open an issue. Security problems: see [SECURITY.md](SECURITY.md); please do not
open a public issue for them.
