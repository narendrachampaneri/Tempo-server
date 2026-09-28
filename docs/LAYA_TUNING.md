# Tuning Laya on Tempo's own decisions

Laya's stock checkpoints are close to chance on Tempo's decisions (median top probability 0.51 on
the questions Tempo asks), so Tempo runs them in **shadow mode**: Laya predicts, the rules
decide, and both are logged. Fine-tuning on Tempo's own outcomes is what makes Laya useful. Laya's
own fine-tune on 6,000 typed decisions raised accuracy from 0.362 to 0.766 on its benchmark.

Everything below follows the software-only rule: Tempo itself runs on CPU; the one GPU step is an
offline training job on Kaggle's free notebooks.

## 1. Collect decisions (days, on CPU)

Tempo logs every question it answers. To get enough varied data without personal data, let
`tempo collect` ask questions from openly licensed public datasets:

```bash
tempo collect --list        # the datasets, their licences, and what their cards say about personal data
tempo collect --estimate    # how many questions and days, from your keys' free limits
tempo collect               # runs until done or stopped (Ctrl-C); run it again to resume
tempo collect --status      # progress per dataset
```

- One question at a time, at most 2 a minute (`--per-minute`), and it keeps half of every
  model's daily free quota for real users (`--reserve 0.5`). It never works around a limit:
  when no model has free quota left it waits for the next quota window (`--no-wait` stops
  instead, and a later run resumes).
- Providers whose terms say outputs may not be used for training (`tempo terms`: Google AI
  Studio) are left out by default, since their answers could never be exported.
- **Local only, all "yes":** `tempo collect --yes-only` uses only models whose outputs may be
  training data, for every stage including the judge. Today that means local Ollama models
  under Apache-2.0 or MIT (for example Qwen or gpt-oss; Tempo reads each installed model's
  licence). With only Ollama running, nothing leaves your computer and no free quota is used;
  the pace (`--per-minute`) and your CPU are the only limits.
- Cerebras is never used for collecting.
- Items that look like they contain an email, phone number, IP address or card number are
  skipped. Each question keeps its dataset's name, and every exported row says which licence
  its text came under.
- Target: about 7,000 labelled decisions, roughly 800 questions. `tempo collect --estimate`
  replaces the assumed numbers with measured ones after 20 questions.

**Where to run it for free:** the simplest place is your own computer. Collection is slow on
purpose, needs no GPU and resumes after any stop, so running it for a few hours a day works.
Laya itself is not needed for collection (`TEMPO_LAYA=off` saves memory); the labels come from
outcomes, not from Laya's predictions.

## 2. Export the dataset

```bash
tempo terms                                  # read each provider's verdict and quoted terms first
tempo export-laya --out laya-dataset         # only providers whose terms say "yes"
tempo export-laya --out laya-dataset --include-unclear   # after you have read the "unclear" ones
```

This writes `train.jsonl`, `test.jsonl` (10% of questions, split by question) and a `README.md`
listing the workflows, the source datasets and their licences. Rows from CC-BY-SA sources (Dolly)
must be shared under the same licence if you publish the dataset or the model.

## 3. Fine-tune on Kaggle's free GPUs (offline, about 10 minutes)

1. Open Laya's notebook
   [`notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb`](https://github.com/NandhaKishorM/laya/blob/main/notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb)
   in Kaggle (File → Import notebook), and set **Accelerator: GPU T4 x2** and **Internet: on**.
   Kaggle gives about 30 GPU hours a week for free; this job takes minutes.
2. Upload the `laya-dataset` folder (Add data → Upload → New dataset).
3. In section 3, replace the `load_dataset("LocalLLaMA/typed-decisions", ...)` line with
   `load_dataset("json", data_files="/kaggle/input/<your-dataset>/train.jsonl", split="train")`,
   and in section 6 load `test.jsonl` the same way. The dataset's `README.md` has the exact
   lines, including the workflow names for the report in section 9.
4. Run all. The checkpoint is written to `/kaggle/working/laya_finetuned_typed_decisions`.
5. Get it out: either download that folder from the notebook's Output tab, or run section 8 with
   your own Hugging Face repo name (`NEW_REPO = "<you>/tempo-laya"`, private is fine) and an
   `HF_TOKEN` Kaggle secret.

## 4. Use it in Tempo (CPU)

```bash
export TEMPO_LAYA_MODEL=/path/to/laya_finetuned_typed_decisions   # or <you>/tempo-laya
tempo serve
```

On first load Tempo exports the checkpoint to ONNX for the CPU runtime (a minute or two, cached
in `~/.tempo/laya/`), measures how long each kind of call takes on this machine, and sets the
time limit from that. `tempo laya status` shows the runtime, threads and limit. The new
checkpoint starts in shadow mode like the stock one.

## 5. Let it take over where it wins

After a few hundred more questions (real ones, or `tempo collect` again):

```bash
tempo laya compare                                   # Laya vs the rules on held-out questions
export TEMPO_LAYA_TAKEOVER="should_stop=auto, next_model=auto, quality=auto"
```

`auto` hands a decision to Laya only when `tempo laya compare` shows it beating the rules on at
least 50 held-out rows, and only counts predictions made by exactly this checkpoint and runtime.
Re-run the comparison after every new checkpoint.
