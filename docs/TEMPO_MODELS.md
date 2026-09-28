# Tempo's own models: plan

_Plan and data only (step 3, 2026-09-28). Nothing has been trained yet. Facts entered by hand
carry a source and a date; estimates are marked as estimates and are replaced by measurements
on the first run._

Tempo is **open models plus the system that runs and trains them**. The system (routing,
checking, the staged engine) already works with free APIs. Tempo's own models are trained from
Tempo's own **checked** runs, run on an ordinary CPU (the software-only rule), and are released
openly. Tempo uses them first and calls free APIs only when they are not confident.

| Model | What it does | Base (licence) | Trained from | Ships as | Status |
|---|---|---|---|---|---|
| **Tempo-Router** | Picks the model for each stage, and decides stop or continue | Laya (`convaiinnovations/laya`, Apache-2.0, 421M parameters) | `tempo export-laya`: plan, pick and assess rows | Laya checkpoint, PyTorch fp32 on CPU (ONNX fp32 optional) | started: Laya runs in shadow mode, rows are logged and exported |
| **Tempo-Judge** | Grades answers (Laya's `score` type), to save judge calls to big models | Laya (Apache-2.0) | graded answers: the judge's grade, heuristics, 👍/👎 | same | data logged (every check); export and training planned |
| **Tempo-Core** | Writes answers itself, on CPU | a 1–4B Apache-2.0 or MIT model (below) | `tempo export-sft`, then `tempo export-pairs` | GGUF, 4-bit (Q4_K_M), for llama.cpp or Ollama | data export ready; training planned |
| **Tempo Tune add-ons** | One small adapter per user scenario | Laya checkpoints (decisions) or LoRA on Tempo-Core (writing) | per scenario (ARCHITECTURE §13) | Laya checkpoint, or GGUF LoRA adapter | Phase 5 |

Base-model licences, from each model's Hugging Face page (checked 2026-09-28):
Qwen3-1.7B (2.0B parameters) and Qwen3-4B, Apache-2.0; IBM Granite 3.3 2B Instruct, Apache-2.0;
SmolLM3-3B, Apache-2.0; Phi-4-mini-instruct (3.8B), MIT. Gemma 3 1B uses the Gemma licence
(not Apache or MIT), so it is not a candidate. **First choice: Qwen3-1.7B** (small enough for
a 4-core CPU at a usable speed, multilingual), with Granite 3.3 2B as the second family for
comparison (owner's decision, step 4).

**4B option** (Qwen3-4B, or Phi-4-mini): trained the same way, then measured on 4 CPU threads.
It is offered only if it reaches **at least 8 tokens a second** on 4 threads and **wins on the
held-out set** against the 1.7B model (per task type, §3); otherwise only the 1.7B model ships.

---

## 1. Data: only checked, "yes" rows

All three datasets come from Tempo's log and follow rule 6 (only sources marked "yes", with the
licence and source on every row):

| Export | Row | Used for |
|---|---|---|
| `tempo export-laya` | a typed decision with its outcome-based label | Tempo-Router, Tempo-Judge |
| `tempo export-sft` | the conversation and the **final answer that passed its check** (heuristics plus a judge from another family), no 👎 | Tempo-Core SFT |
| `tempo export-pairs` | the prompt, **chosen** = that answer, **rejected** = an earlier answer to the same question that failed its check (hard failures first) | Tempo-Core DPO |

- A row is exported only if **every** model that wrote or graded text for its question is
  "yes": local Apache-2.0/MIT models, Mistral text outputs, and Apache-2.0/MIT models on
  Cloudflare. Before every export, the terms of hosted "yes" providers that appear in the log
  are re-read (`tempo terms --check`); if a quote has changed, that provider is left out.
- Questions come from openly licensed public datasets (`tempo collect`; questions only) or from
  the owner. Other users' questions need their consent first (Phase 3), so they are left out by
  default (`--user NAME` adds one).
- **The same held-out split everywhere**: a question's split comes from a hash of its id, so it
  is in the test set of every export and never in any training set.

## 2. Training plan per model

All training runs **offline on a free Kaggle notebook** (2×T4, about 30 GPU hours a week and
sessions of up to 12 hours, as recorded in ARCHITECTURE.md §1.1 on 2026-09-28; Kaggle's docs
page is drawn with JavaScript and could not be re-read here, so check the current quota on the
notebook's settings panel). Nothing that serves a request depends on the notebook.

### Tempo-Router and Tempo-Judge (Laya)

1. `tempo export-laya` (plan, pick and assess rows; the judge rows are the `quality` score
   questions).
2. Laya's own notebook (`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb`), as in
   [LAYA_TUNING.md](./LAYA_TUNING.md). Laya's own fine-tune on 6,000 decisions took about 10
   minutes; **estimate 10–30 minutes** for 5,000–20,000 of Tempo's rows.
3. Ship the checkpoint as PyTorch fp32 (the CPU default; ONNX fp32 gives identical answers, and
   INT8 cost 8 accuracy points, [LAYA_CPU.md](./LAYA_CPU.md)).
4. Tempo-Judge answers first; a big-model judge is called only when its top probability is below
   `TEMPO_LAYA_MIN_CONFIDENCE` or the question is in `best` mode. Target: half of today's judge
   calls saved, with no drop in how often checked answers get 👎.

### Tempo-Core (a 1–4B writing model)

| Stage | Method | Data | Estimate on Kaggle 2×T4 (to be measured) |
|---|---|---|---|
| 1. SFT | LoRA (QLoRA, 4-bit base, rank 16) with TRL's `SFTTrainer` or Unsloth | `export-sft`, 2–10k rows, plus the public share (§4) | 1.7B: about 1 hour for 5k rows of ~600 tokens, 2 epochs; 4B: about 2–3 hours |
| 2. DPO | TRL's `DPOTrainer` on the SFT model, same LoRA | `export-pairs`, 1–5k pairs | 1.7B: about 1 hour for 2k pairs |
| 3. Rewards from checks (later) | GRPO (TRL's `GRPOTrainer` or Unsloth) with rewards computed by Tempo's own checkers: maths answers compared numerically, code run against its tests in the sandbox (Phase 3's WebAssembly sandbox; in the notebook, the same checker code) | maths and code questions from openly licensed datasets (GSM8K, MIT; MBPP, CC-BY-4.0) | 1.7B: 4–8 hours for about 500 prompts × 4–8 samples; fits one 12-hour session |

Then, in the same notebook, on its CPU:

- merge the LoRA into the base, convert with llama.cpp's `convert_hf_to_gguf.py`, and quantize
  to **Q4_K_M** (`llama-quantize`): about 1.1 GB for 1.7B, 2.5 GB for 4B (estimates);
- measure speed on a 4-core CPU: it must reach about 10 tokens a second (ARCHITECTURE.md §1.1),
  or it is not registered;
- run the promotion gate (§3).

Tempo loads it through Ollama (`ollama create tempo-core -f Modelfile`) or llama.cpp's server,
both on CPU.

### Tempo Tune add-ons

- **Decision scenarios**: a Laya checkpoint per scenario, trained like Tempo-Router (minutes).
- **Writing scenarios**: a LoRA adapter on Tempo-Core, converted to GGUF with llama.cpp's
  `convert_lora_to_gguf.py`. One llama.cpp server holds the base once and several adapters
  (`--lora a.gguf --lora b.gguf`), and picks them per request (the `lora` request field;
  requests with different adapters are not batched together, per llama.cpp's server README,
  checked 2026-09-28). Estimate: 10–30 minutes per adapter for 500–2,000 examples.

## 3. Promotion gate: a new version replaces the old one only if it wins

- **Fixed held-out test set**: the test split of the exports (never trained on), frozen per
  release as a list of question ids, plus Tempo's probe set (`tempo/data/evalset.yaml`, graded
  by rules). The set is stored with the release; it only grows, never changes.
- **Graded the same way for both versions**: rule grades (numbers, JSON, code parses or passes
  its tests), the checker's heuristics, and a judge from another family that sees both answers
  in random order.
- **Rule, per task type** (owner's decision, step 4): the task types are chat, code, maths,
  reasoning, writing, summarize, translate and extract. For each task type, the new version is
  used **only if** that type has **at least 30 held-out questions** and the new version's mean
  score **is not lower** than the old one's; every other task type keeps the old version.
  Tempo routes per task type, so one release can serve new and old versions side by side. A
  version is released at all only if it wins overall on the task types it takes over (more wins
  than losses on paired questions, with a 95% bootstrap interval of the score difference above
  zero), runs fast enough on a 4-core CPU, and passes the repetition check (§4).
- **Otherwise the old version stays** (for that task type, or entirely), and the reason is
  logged: which tasks dropped or had too few questions, by how much, the win/loss counts and the
  repetition numbers. The log lives next to the model files
  (`PROMOTIONS.md` per model) and in the release notes, so a rejected version is never lost.
- The same gate applies to Tempo-Router and Tempo-Judge, with `tempo laya compare`'s agreement
  with outcomes as the score (per decision, like per task).

## 4. Model-collapse protection

Training a model on model-written text again and again narrows it. Tempo guards against that in
every run:

1. **Only checked answers**: answers that passed the heuristics and a judge from another model
   family, with no 👎 (§1). Failed drafts are used only as the "rejected" side of DPO pairs.
2. **A share of public or human data in every run**: at least 30% of each SFT run comes from
   human-written answers in openly licensed datasets (for example GSM8K's worked solutions,
   MIT; MBPP's reference code, CC-BY-4.0; OpenAssistant conversations, Apache-2.0) and from
   answers the owner gave a 👍. Their licences are recorded on every row like the rest.
   **Dolly (CC-BY-SA-3.0) is kept out of every training export** (Tempo-Core, Tempo-Router and
   Tempo-Judge): its rows go to the held-out test split only (owner's decision, step 4). As a
   permissive replacement for general questions, OpenAssistant's `oasst2` (Apache-2.0 on its
   Hugging Face page, checked 2026-09-28) is proposed.
3. **Where the answers came from**: answers written by an earlier Tempo-Core version may make up
   at most 30% of a run; the rest come from other models and from people. Each row records its
   writing model, so the share is counted, not guessed.

Both numbers are settings, `TEMPO_MIN_PUBLIC_SHARE=0.3` and `TEMPO_MAX_SELF_SHARE=0.3`, as
starting values; `tempo export-sft` reports the mix and warns when it is off. The collapse check
(below) and the promotion gate (§3) guide any change.
4. **Repetition check between versions**: `tempo export-sft` already reports two measures of
   the answers (the share of distinct word pairs, and the share of 4-word sequences repeated
   within an answer). On the held-out questions, a new version may not be more repetitive than
   the old one by more than 5% on either measure, or it is not promoted (§3).

## 5. Release plan

- **Where**: Hugging Face model repositories under the owner's account, named after the
  project, Tempo-server (placeholders until the account exists: `<owner>/tempo-server-router`,
  `<owner>/tempo-server-judge`, `<owner>/tempo-server-core-1.7b-gguf`), each with a model card.
- **Licences**: Tempo-Router and Tempo-Judge inherit Laya's Apache-2.0. Tempo-Core inherits its
  base model's licence (Apache-2.0 for Qwen3, MIT for Phi-4-mini). The training data's licences
  are listed in the card; data under share-alike licences is kept out (§4).
- **Dataset credits** go in each model card: every dataset used, its licence and the
  attribution it requires, for example "MBPP (Mostly Basic Python Problems) by Google Research,
  licensed under CC BY 4.0", and GSM8K and HumanEval by OpenAI under MIT.
- **Model card**: base model and licence; what it is for and not for; training data summary
  (sources, licences, counts, the "yes" rule and the date terms were checked); training method
  and settings; held-out results against the previous version and against the free APIs it
  replaces, per task; CPU speed and memory on a 4-core machine; known limits (languages
  covered, maths and code without a sandbox); how to run it with Tempo, Ollama or llama.cpp.
- **Datasets**: published only if every row's licence allows it; otherwise kept private and the
  card says so.
- **Running order in Tempo**: Tempo loads its own models first. Tempo-Router makes the
  decisions it has won (`TEMPO_LAYA_TAKEOVER=auto`). Tempo-Core drafts first for the tasks where
  it passed the gate; Tempo-Judge grades it. If the judge is not confident, or the answer fails
  its check, the staged engine escalates to the free APIs exactly as today. A model that is
  missing, slow or not confident never blocks an answer.

## 6. What each step needs before it can start

| Step | Needs |
|---|---|
| Tempo-Router v1 | a few thousand "yes" decision rows: `tempo collect --yes-only` with Ollama on the owner's computer |
| Tempo-Judge v1 | the same run (every check is logged) |
| Tempo-Core SFT | 2k+ checked "yes" answers, plus the public share |
| Tempo-Core DPO | 1k+ pairs (questions whose draft failed and a later answer passed) |
| GRPO | the WebAssembly sandbox (Phase 3) for code rewards |
| Release | the code licence (Apache-2.0, chosen in step 4) and a Hugging Face account |
