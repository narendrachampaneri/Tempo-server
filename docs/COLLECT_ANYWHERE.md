# Running `tempo-server collect` on Windows, or as a scheduled GitHub Actions job

`tempo-server collect` makes Laya training data slowly, within every free limit, and resumes where it
stopped ([LAYA_TUNING.md](./LAYA_TUNING.md)). Two ways to run it for free:

- **A. On a Windows computer**, with local models through Ollama: nothing leaves your computer,
  no free quota is used, and every answer is training data that counts as "yes".
- **B. As a scheduled GitHub Actions job**, with provider keys stored as repository secrets:
  short runs every few hours, resuming across runs.

Rules that apply to both (see [CLAUDE.md](../CLAUDE.md)): each person uses their own keys,
nothing works around a rate limit, and rows are exported only from sources marked "yes".
`--yes-only` enforces that at collection time.

---

## A. Windows

Tested steps for Windows 10 or 11 on an ordinary CPU (16 GB RAM for two 8B models; 8 GB for the
smaller pair below).

### 1. Install Ollama and Tempo-server (once)

Open **PowerShell** and run:

```powershell
winget install -e --id Ollama.Ollama
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/narendrachampaneri/Tempo-server/main/install.ps1 | iex"
```

The second line installs Tempo-server as its own tool (no Python or virtual environment to
manage) and starts `tempo-server setup`: press Enter at each key for a local-only run. Close and
reopen PowerShell so the new commands are found. (Without `winget`, use the installer from
ollama.com.)

### 2. Get two open-licence models (once)

The judge must come from a different model family than the writer, so pull two families. Both
of these are Apache-2.0 (their licences on ollama.com, checked 2026-09-28):

```powershell
ollama pull qwen3:8b
ollama pull granite3.3:8b
```

With 8 GB of RAM, use smaller sizes instead (check each with `ollama show --license <model>`;
Tempo counts only Apache-2.0 and MIT as "yes"):

```powershell
ollama pull qwen3:4b
ollama pull granite3.3:2b
```

### 3. Point Tempo-server at Ollama

```powershell
tempo-server setup --only ollama     # finds the running Ollama and remembers it
tempo-server doctor                  # everything should say ✓ (keys are optional here)
```

### 4. Check, then collect

```powershell
tempo-server models                         # both local models should say "ready"
tempo-server terms                          # read what "yes" means for each source
tempo-server collect --estimate --yes-only  # how long, at your pace
tempo-server collect --yes-only             # runs until done; Ctrl-C any time
```

Run `tempo-server collect --yes-only` again whenever you like: it resumes where it stopped. Progress,
logs and quota counters live in `%LOCALAPPDATA%\tempo-server`. `tempo-server collect --status` shows progress.

### 5. Optional: run it every evening with Task Scheduler

Create `%USERPROFILE%\tempo-server\collect.cmd`:

```bat
@echo off
cd /d %USERPROFILE%\tempo-server
"%USERPROFILE%\.local\bin\tempo-server.exe" collect --yes-only --no-wait --minutes 120 >> collect.log 2>&1
```

(That is where the installer puts `tempo-server.exe`; `where.exe tempo-server` shows yours.)

Then, in PowerShell:

```powershell
schtasks /Create /SC DAILY /ST 20:00 /TN "Tempo collect" /TR "%USERPROFILE%\tempo-server\collect.cmd"
```

Ollama must be running (it starts with Windows after installing). In Power settings, stop the
computer from sleeping during the run. `--minutes 120` stops starting new questions after two
hours; the next evening resumes.

### 6. Export

```powershell
tempo-server export-laya --out laya-dataset
```

---

## B. A scheduled GitHub Actions job

The example workflow is [docs/examples/collect-workflow.yml](./examples/collect-workflow.yml). It
is not active until you copy it to `.github/workflows/collect.yml` (the owner decides; see
docs/STATUS.md).

### How it stays within every limit and resumes

- **One run at a time** (`concurrency`), every 6 hours, at most 50 minutes each
  (`--minutes 45`), 2 questions a minute at most, and half of every daily free limit kept for
  real users (`--reserve 0.5`, the default).
- **`--no-wait`**: when no model has free quota left, the run stops instead of waiting; the next
  scheduled run carries on. Nothing retries a model that said 429, and no extra keys are used.
- **Resumable**: the data directory (progress, the question log and the per-day quota counters)
  is saved with `actions/cache` at the end of every run, even a failed or cancelled one, and
  restored at the start of the next. Because the quota counters travel with it, each run knows
  how much of today's free allowance the earlier runs used. Rate-limit headers from providers
  correct the counts if the same key is also used elsewhere.
- **Only "yes" data is kept by default**: the export step runs `tempo-server export-laya` without
  `--include-unclear`.

### Setting it up

1. Use a **private** repository (the cache holds the question log; secrets are never written to
   it).
2. In the repository: Settings → Secrets and variables → Actions → New repository secret. Add
   only the keys you have, for example `GROQ_API_KEY`, `OPENROUTER_API_KEY`,
   `MISTRAL_API_KEY`, `CLOUDFLARE_API_TOKEN`; add `CLOUDFLARE_ACCOUNT_ID` as a *variable*
   (it is not a secret). Also add `TEMPO_SECRET_KEY` (any long random string) so no key-vault
   file is written. Never add Cerebras or Cohere: they are blocked for collecting.
3. Copy `docs/examples/collect-workflow.yml` to `.github/workflows/collect.yml` and push.
4. Run it once by hand: Actions → "Tempo collect" → Run workflow. Check the log, then let the
   schedule run.
5. Download the dataset from the run's artifacts (`laya-dataset`) when enough has been
   collected (`tempo-server collect --estimate` says how much is enough).

### Things to know

- Scheduled workflows in a repository with no activity for 60 days are paused by GitHub; push
  anything or run it by hand to resume.
- Caches not used for 7 days are deleted; a 6-hourly schedule keeps it fresh. If the cache is
  lost, the next run starts a new data directory: already exported data is safe in the
  artifacts, and collection starts over from the first question.
- GitHub's Actions terms ask that Actions be used for the project's own software work; this job
  collects training data for Tempo's own decision model. Keep runs short and infrequent.
- Hosted API providers are "unclear" for training today, so their answers are collected but
  not exported unless the owner decides otherwise (`--include-unclear` stays off). With keys
  only for "unclear" providers, the job builds a log for later, not a dataset.
