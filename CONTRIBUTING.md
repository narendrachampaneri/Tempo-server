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
6. **Training data.** Only from sources marked "yes" (`tempo terms`), with the licence and
   source on every row.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                  # the full suite runs offline
ruff check . && ruff format --check .      # lint
TEMPO_ENABLE_MOCK=1 tempo serve            # demo models, no keys needed
```

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
4. `tempo terms --check` must find every quote.

## Reporting problems

Bugs and ideas: open an issue. Security problems: see [SECURITY.md](SECURITY.md); please do not
open a public issue for them.
