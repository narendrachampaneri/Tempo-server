# Training Tempo's own models: the whole path

This page covers the whole loop: collect data on your computer, train on Kaggle's free GPUs,
bring the models back, and keep them only if they are better. Three models are trained
(docs/TEMPO_MODELS.md):

| Model | Notebook | What it learns | Output |
|---|---|---|---|
| **Tempo-Core** (Qwen3-1.7B, Apache-2.0) | `training/tempo_core.ipynb` | answers that passed Tempo's checks (SFT), then which answer beats a failed draft (DPO) | `tempo-core-q4_k_m.gguf` for Ollama |
| **Tempo-Router and Tempo-Judge** (Laya, Apache-2.0) | `training/tempo_router_judge.ipynb` | Tempo's routing decisions and answer grades, labelled by what happened | a Laya checkpoint |

Only your computer and a free Kaggle account are needed. No paid service, and no GPU on your
side: the GPU work happens once, offline, on Kaggle. Everything Tempo-server runs afterwards
works on an ordinary CPU.

```
collect ─▶ train prepare ─▶ upload to Kaggle ─▶ run ─▶ download ─▶ models import ─▶ models compare ─▶ models promote
(your PC)   (your PC)        (browser)          (Kaggle GPU)       (your PC, CPU)
```

## 0. Try the whole loop first (no keys, no GPU, about 5 minutes)

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu   # Linux: PyTorch's CPU build (skip on Windows/macOS)
pip install "tempo-server[train]"
tempo-server train dry-run --work ./dry-run
```

This runs every step below on your CPU with tiny models and the offline demo models: 40 demo
questions, `train prepare`, both notebooks cell by cell (100 LoRA steps on a 40M-parameter
copy of Qwen3's architecture, 20 DPO steps, a small Laya), GGUF conversion, `models import`,
`models compare` and `models promote`, in a private folder (`./dry-run/data`), so your real
data is not touched. It proves the plumbing, not quality: the tiny Tempo-Core writes nonsense,
so its gate says "the old version stays", which is the correct result. CI runs it on Linux on
every push. The GGUF step (llama.cpp's converter and binaries) runs on Linux x86-64 only, as on
Kaggle; on Windows or macOS, run the dry run in WSL or skip it (CI covers it).

The training libraries are an optional extra (`tempo-server[train]`: PyTorch, Transformers,
PEFT, TRL, Datasets, Laya, llama.cpp's `gguf`). The normal install never needs them.

## 1. Collect (your computer, days, CPU)

Training data only comes from sources marked "yes" (rule 6): your local Apache-2.0 or MIT
models through Ollama, Mistral's text outputs, openly licensed public questions, and your own
👍. Run collection on your computer, as in [LAYA_TUNING.md](./LAYA_TUNING.md) §1 and
[COLLECT_ANYWHERE.md](./COLLECT_ANYWHERE.md):

```bash
ollama pull qwen3:8b            # Apache-2.0; a second family helps the judge: granite3.3:8b
tempo-server collect --estimate --yes-only
tempo-server collect --yes-only             # stop any time; run again to resume
```

How much is enough for a first real run (docs/TEMPO_MODELS.md §6): about **2,000 checked
answers** (SFT), **1,000 pairs** (a draft failed and a later answer passed), **3,000 labelled
decisions** (Laya), and **30+ held-out questions per task type** you want Tempo-Core to take
over. `tempo-server train prepare` tells you where you stand. At 2 questions a minute on a
laptop that is roughly 2–4 days of collection in the background (an estimate; `collect
--estimate` measures it after 20 questions).

## 2. Prepare the upload (your computer, a minute)

```bash
tempo-server train prepare
```

It:

1. runs the three exports (`export-laya`, `export-sft`, `export-pairs`) with the same held-out
   split (10% of questions, by a hash of their id) and re-checks the terms of hosted "yes"
   providers first;
2. checks every **training** row: a source with a licence, no share-alike or non-commercial
   source (Dolly stays in the test split), every model that wrote or graded it is "yes".
   Any problem stops it;
3. checks the **data mix**: at least `TEMPO_MIN_PUBLIC_SHARE` (30%) public or human data and at
   most `TEMPO_MAX_SELF_SHARE` (30%) answers from an earlier Tempo-Core. A mix problem stops it
   unless you pass `--force` (recorded in the pack);
4. notes what is small (rows below the recommended counts, task types with fewer than 30
   held-out questions);
5. writes `tempo-pack.json` (counts, mix, licences per source and per answer model, checksums),
   adds the training kit (`tempo_trainkit.py`) and packs **one zip**:
   `<data dir>/training/tempo-training-<date>.zip`, with both notebooks next to it in
   `<data dir>/training/notebooks/`;
6. prints the exact upload steps (below).

## 3. Upload to Kaggle (browser, 5 minutes, once per pack)

You need a free Kaggle account, **phone-verified** (Kaggle requires that for GPUs and for
Internet in notebooks). Kaggle's free quota is about 30 GPU hours a week, at most 12 hours per
session (recorded 2026-09-28 in ARCHITECTURE.md §1.1; check the current numbers in your
notebook's settings panel). Kaggle's pages change; these steps were written on 2026-09-29.

1. kaggle.com → **Create → New Dataset**. Drag in the zip. Title: `tempo-training` (any name).
   Keep it **Private**. **Create**. Kaggle unpacks the zip (the notebooks also accept it
   packed).
2. **Create → New Notebook → File → Import Notebook**, upload `tempo_core.ipynb`. Make a second
   notebook the same way for `tempo_router_judge.ipynb`.
3. In each notebook's right panel: **Session options → Accelerator: GPU T4 x2**,
   **Internet: On**. **Add Input → Datasets → Your Work → tempo-training**.

## 4. Run (Kaggle, in the background)

**Save Version → Save & Run All (Commit)**. The run continues after you close the browser.
Each notebook:

- checks for a GPU (and stops with the fix if there is none);
- installs its libraries (a minute or two);
- prints a progress line every minute: step, share done, time elapsed, time left for the
  stage, loss, and time left in the session;
- saves checkpoints as it goes and **stops cleanly 20 minutes before its time budget**
  (`TIME_BUDGET_HOURS = 11`, Kaggle's limit is 12);
- writes `report.json` and `REPORT.md`: status, losses, held-out scores, data mix, licences
  (base model, every data source, every answer model), files with SHA-256, times.

How long (estimates until the first real run measures them; the report records the real
times):

| Notebook | Data | Estimate on Kaggle's T4 |
|---|---|---|
| Tempo-Core | 5,000 SFT rows × 2 epochs, 2,000 pairs | SFT about 1 hour, DPO about 1 hour, GGUF 10–15 minutes; about 2.5 hours in all |
| Tempo-Router/Judge | 5,000–20,000 Laya rows | 10–30 minutes (Laya's own 6,000 decisions took about 10 minutes on 2×T4) |

Tempo-Core trains on one T4 (LoRA rank 16 on the full-precision base; 1.7B fits one T4, so no
4-bit base and no quantisation error before merging). Laya uses both T4s, as its official
notebook does.

**If a run stops at the time limit** (`REPORT.md` says "incomplete"): open the notebook →
**Add Input → Notebook Output →** the version that stopped → **Save & Run All** again. It copies
that version's `checkpoints/` and continues from the last checkpoint; finished stages are
skipped.

## 5. Download (browser)

Open the finished version → **Output** → **Download** (a zip of `/kaggle/working`). Read
`REPORT.md` first: status "complete", held-out loss after SFT below the loss before, DPO reward
accuracy above 0.5, and for Laya held-out accuracy after above before.

## 6. Import (your computer)

```bash
tempo-server models import ~/Downloads/tempo-core-output.zip
tempo-server models import ~/Downloads/tempo-router-judge-output.zip
```

- Tempo-Core: the GGUF is checked against the report's SHA-256, copied to
  `<data dir>/models/tempo-core/<version>/` and **registered with Ollama as
  `tempo-core:<version>`** (Ollama must be running; otherwise it prints the `ollama create`
  command to run later, or run `import` again). Its Modelfile carries the chat template
  Tempo-Core was trained with, the stop word and the Apache-2.0 licence (so Tempo counts its
  outputs as "yes").
- Laya: the checkpoint is checked, copied to `<data dir>/models/laya/<version>/` and
  test-loaded on the CPU.

Nothing answers questions until it is promoted.

## 7. Compare (your computer, CPU; minutes to an hour)

```bash
tempo-server models compare                  # the last imported kind; or --kind tempo-core / --kind laya
```

- **Tempo-Core**: the pack's held-out questions (never trained on) are answered by the old
  version (the promoted Tempo-Core, or for the first run the untuned base `qwen3:1.7b` from
  Ollama: `ollama pull qwen3:1.7b`) and the new one, and each answer is graded the same way:
  Tempo's quick checks, a judge from another model family, and for code and arithmetic a run in
  the sandbox (a failed run scores 0). Other runners: `--old ollama:NAME`, `--old gguf:PATH`
  (llama.cpp's `llama-server`). With about 300 held-out questions and a judge on free APIs,
  expect 20–60 minutes, about 600 judge requests (they count against your free quotas).
- **Laya**: both checkpoints predict every decision in the held-out rows on the CPU; accuracy per
  decision against the outcome labels. Needs `tempo-server[laya]`.

The **promotion gate** (docs/TEMPO_MODELS.md §3):

1. per task type (Tempo-Core): the new version takes a type only with **30+ held-out
   questions** and a mean score **not lower** than the old one's; other types keep the old
   version;
2. the new version is released at all only if, on the types it takes, it has **more wins than
   losses** on paired questions, the **95% bootstrap interval** of the score gain is **above
   zero**, it is **not more repetitive** than the old version (by more than 5%), and it runs at
   **8+ tokens a second** on this computer's CPU (`--min-speed`);
3. Laya: per decision, 50+ held-out rows and no drop; since one checkpoint serves every
   decision, it is promoted only if no decision drops and at least one improves.

## 8. Promote (your computer, instant)

```bash
tempo-server models promote
```

- Tempo-Core: each task type it won is routed to `tempo-core:<version>`
  (`<data dir>/models/tempo-core-routes.json`); the router skips a Tempo-Core version for every
  other task type, so old and new versions serve side by side. Restart `tempo-server serve`.
- Laya: `TEMPO_LAYA_MODEL` is set in `settings.env`. The checkpoint starts in shadow mode;
  `tempo-server laya compare` and `TEMPO_LAYA_TAKEOVER` hand it the decisions it wins
  ([LAYA_TUNING.md](./LAYA_TUNING.md) §5).

If the gate is not passed, nothing changes and the reasons are printed; the comparison is kept
in `<data dir>/models/models.json`.

## What "good enough" looks like

| Check | Good enough to promote |
|---|---|
| Notebook report | status complete; held-out SFT loss after < before; DPO held-out reward accuracy > 0.5; Laya held-out accuracy after > before |
| Tempo-Core per task type | 30+ held-out questions, mean score ≥ the old version's |
| Tempo-Core overall | more wins than losses, 95% interval of the gain above 0, repetition within 5%, 8+ tokens/s on your CPU |
| Laya | no decision with 50+ rows drops, at least one improves |
| First release target (TEMPO_MODELS.md) | Tempo-Core takes at least chat and maths; Tempo-Judge saves half of the judge calls with no rise in 👎 |

## What needs what

| Step | Needs |
|---|---|
| dry run | `tempo-server[train]`, Linux x86-64 for the GGUF step; no keys, no GPU |
| collect | Ollama with an Apache-2.0/MIT model (or Mistral with a key) |
| prepare, import, compare, promote | your computer; compare needs Ollama (Tempo-Core) or `tempo-server[laya]` (Laya), and a judge model |
| upload, run, download | a free, phone-verified Kaggle account |
