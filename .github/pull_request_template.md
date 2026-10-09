## What and why

<!-- One change per pull request: what it does and why. Link the issue if there is one. -->

## How it was tested

<!-- The tests you added or ran, and anything checked by hand (demo mode is fine). -->

## Checklist

- [ ] Tests and lint pass: `pytest -q && ruff check . && ruff format --check .`
- [ ] New behaviour has a test; a bug fix has a test that failed before it
- [ ] Docs updated in the same change (README, docs/, docs/STATUS.md where it applies)
- [ ] No real keys anywhere (code, tests, logs, screenshots); tests make no network calls
- [ ] Follows the rules in CONTRIBUTING.md (CPU only, free only, live facts with source and date, provider terms)
