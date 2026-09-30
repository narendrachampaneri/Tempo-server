# Rules for every session on Tempo

These rules come from the project owner. Every session follows them. Read `docs/STATUS.md` first
to see what is done, in progress, blocked and next.

1. **100% software.** Nothing may need a GPU or special hardware to run. It must work on an
   ordinary CPU computer or a free cloud service. Free notebooks like Kaggle are allowed only for
   offline training.
2. **Free and open source.** Only free tiers and free or open models. No paid API may be required.
3. **Read model lists and limits live from each provider.** Seed lists are only a fallback, and
   every hand-entered fact gets a source link and a date.
4. **Keys.** The proxy adds keys from this environment's API credentials. Use placeholder key
   values. Never print, log or commit a key. `CLOUDFLARE_ACCOUNT_ID` is a normal environment
   variable (not a secret).
5. **Respect every provider's terms.** Never work around a rate limit, never pool accounts, and
   never use keyless tricks (for example calling OpenCode Zen's free pool without a key). Each
   user brings their own keys.
6. **Training data** only from sources marked "yes", with the licence and source on every row.
   `--include-unclear` stays off, and Cerebras isn't used for eval or collect, until the owner
   decides.
7. **Small commits**, tests and lint green, push after each part, docs updated with each change.
8. **Don't stop to ask.** Make reasonable choices, note them, and list decisions for the owner at
   the end.
9. **Keep `docs/STATUS.md` current** (done, in progress, blocked, next), so a new session can
   resume.
10. **No provider keys are added yet.** Wherever a key is missing, build and test with mocks, mark
    the item "needs key" in `docs/STATUS.md`, and keep a list of what to run once keys exist.
    Public data needs no key (OpenRouter's model and status lists, NVIDIA's model list), so use
    it for real.

## Branches and pull requests (the owner's workflow, 2026-09-30)

11. **One step = one branch = one pull request into `main`.** Start the branch from the latest
    `main`. A fix found later is its own step, with its own branch and pull request.
12. **Never push to `main` directly.** Every change reaches `main` through a pull request.
13. **Merge only when CI is green on Linux, Windows and macOS** for the pull request's latest
    commit. The branch push runs the Linux jobs; the pull request adds Windows, macOS, and the
    install and SDK tests on all three, so both runs must be green. Merge with a merge commit (the
    step's small commits stay as they are).
14. **Then delete the branch. `main` is the only branch that stays.** If the session can't delete
    it (the environment has refused with HTTP 403 so far), list it in `docs/STATUS.md` under
    "Blocked: needs the owner" for the owner to delete on GitHub.

The full CI on `main` (every job on all three systems) is a manual run: Actions → CI → Run
workflow on `main`. A push to `main` runs the Linux jobs only while the repository is private.

## Working in this repo

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q                                   # full suite, offline
ruff check . && ruff format --check .       # lint
TEMPO_ENABLE_MOCK=1 TEMPO_DATA_DIR=memory tempo-server ask "hi"   # offline demo models
```
