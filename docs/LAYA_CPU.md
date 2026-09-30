# Laya on CPU: measurements and the default

Tempo is software-only (docs/ARCHITECTURE.md §1): Laya has to run on an ordinary CPU. This page
records what was measured on 2026-09-28 and why the defaults are what they are.

## Setup

- **Machine:** 4-core Intel Xeon @ 2.80 GHz (AVX-512 VNNI, no bf16), 15 GB RAM, no GPU.
- **Software:** laya 0.3.21, PyTorch 2.14 (CPU build), ONNX Runtime 1.30.
- **Workload:** the exact calls Tempo makes. The staged engine ran over the 96 labelled example
  requests in `tempo/data/task_examples.yaml`, and every Laya call it made was recorded: 419 in
  all. Assess states got realistic answers of mixed quality (code, maths, prose; some wrong,
  some cut off). A fixed subset was timed: 48 plan calls (4 questions each), 68 pick calls (a
  shortlist of up to 10 models) and 61 assess calls (quality and stop/continue).
- **Agreement** is measured against the current setup (PyTorch, English checkpoint, fp32): the
  share of decisions where the variant picks the same answer, and how far probabilities move.
- Each variant ran in its own process after 4 warm-up calls, one call at a time, with nothing
  else using the CPU. The ONNX files were exported with the same recipe as
  `tempo/laya_runtime.py`.

## Results on Tempo's calls (median / 95th percentile per call)

| Variant | Threads | Plan | Pick | Assess | Same answer as current | RAM |
|---|---|---|---|---|---|---|
| PyTorch, English, fp32 (**current**) | 4 | 1489 / 1762 ms | 722 / 889 ms | 754 / 1551 ms | 100% | 2.8 GB |
| PyTorch, English, fp32 | 2 | 2448 / 3933 ms | 1212 / 1494 ms | 1261 / 2640 ms | 100% | 2.8 GB |
| PyTorch, English, fp32 | 1 | 3977 / 4393 ms | 1857 / 2064 ms | 1863 / 4236 ms | 100% | 2.8 GB |
| ONNX Runtime, English, fp32 | 4 | 1405 / 1660 ms | 735 / 883 ms | 734 / 1772 ms | 100% (identical) | 4.5 GB |
| ONNX, English, INT8 (`laya[onnx]` recipe) | 4 | 788 / 909 ms | 474 / 519 ms | 418 / 925 ms | **55.8%** | 1.8 GB |
| ONNX, English, INT8 weights only | 4 | 793 / 1262 ms | 437 / 627 ms | 445 / 978 ms | **55.8%** | 1.8 GB |
| PyTorch, multilingual (smaller encoder) | 4 | 572 / 869 ms | 392 / 462 ms | 327 / 625 ms | 47.9% | 2.3 GB |
| ONNX, multilingual, fp32 | 4 | 584 / 697 ms | 435 / 546 ms | 359 / 735 ms | 47.9% | 3.8 GB |
| ONNX, multilingual, INT8 | 4 | 359 / 428 ms | 304 / 372 ms | 200 / 523 ms | 41.9% | 3.0 GB |
| ONNX, English, fp32, max 256 tokens | 4 | 1430 / 1689 ms | 763 / 965 ms | 731 / 1566 ms | — | 4.5 GB |
| ONNX, English, fp32, shorter states | 4 | 1385 / 1820 ms | 736 / 873 ms | 728 / 1237 ms | — | 4.5 GB |
| PyTorch, English: assess + next pick in **one call** | 4 | — | — | 2703 / 7378 ms (both) | 69.4% | 2.8 GB |

## What this means

- **INT8 is twice as fast but changes the answers.** Per-channel INT8 (Laya's own
  `export_onnx.py --quantize`) picks a different answer on 44% of Tempo's decisions, with top
  probabilities moving by up to 0.84. Even where the current model is confident (top
  probability ≥ 0.6), INT8 agrees only 73% of the time. Keeping the attention maths in fp32 and
  quantizing only weight matrices gives exactly the same result, so the loss comes from the
  weights themselves. Part of the reason: the stock checkpoints are close to chance on Tempo's
  decisions (median top probability 0.51), so a small change flips them.
- **ONNX fp32 is identical but not faster**, and it needs 60% more memory. PyTorch loads in
  6 s against 30 s.
- **The multilingual checkpoint is 2.6× faster** (a base-size encoder instead of a large one),
  but it is a different model: it agrees with the English one on 48% of decisions, which says
  little either way while both are near chance.
- **Threads matter:** 1 → 4 threads is 2.7× faster. Tempo uses up to 4 cores.
- **Shorter inputs barely help.** Tempo's states are already short (question up to 700
  characters, answer trimmed to 650); a 256-token cap changes nothing, and trimming states
  further only shortens the slowest assess calls.
- **One batched call per stage is slower on CPU.** Laya reads the whole state once per
  question, so adding the pick question to the assess call makes it read the answer text too.
  The merged call took 2.7 s against about 1.5 s for the two separate calls, and changed 31% of
  answers. Asking fewer questions on the critical path is what helps (below).

## What Tempo does

1. **Shadow predictions never wait.** While a decision is in shadow mode (the default), its
   answer is not used, so Tempo asks Laya in the background and logs the prediction when it
   finishes. Laya adds no time to an answer until it takes over a decision.
2. **Only taken-over questions are waited for.** When, say, only `should_stop` is taken over,
   that question is asked on its own (one row, not the whole group), and waited-for calls jump
   ahead of background ones on Laya's single worker thread.
3. **The time limit is measured, not fixed.** At load Tempo times a plan, an assess and a pick
   call on this machine and sets the limit to 1.5× the slowest (at least 100 ms, at most 5 s).
   A slower CPU gets a longer limit instead of silently losing every decision to the rules.
   `TEMPO_LAYA_TIMEOUT_MS=<ms>` still fixes it by hand.
4. **Up to 4 CPU threads** (`TEMPO_LAYA_THREADS`).
5. **Shadow predictions wait for idle time while Laya decides something.** A prediction that
   has started cannot be interrupted, so while a question with taken-over decisions is running,
   background predictions are held and run between questions.
6. **Backend: the faster fp32 runner on this machine** (`TEMPO_LAYA_BACKEND=auto`, the default
   since step 10). PyTorch fp32 serves at once. When ONNX Runtime is installed (the `laya`
   extra installs it) and the machine has room for both (12 GB of memory or more), the two
   fp32 runners are timed once between questions, and the faster is kept. The choice is saved
   per checkpoint and machine in `laya/runner.json` in the data folder, so it is made once.
   Both give identical answers. On the machine above ONNX fp32 was not faster, so auto keeps
   PyTorch there; on others it may win. INT8 stays opt-in (`onnx-int8`) because it changes
   answers, including on a fine-tuned checkpoint (next section). `torch` and `onnx` fix the
   runner by hand.
7. **Not asked when the rules are sure** (`TEMPO_LAYA_SKIP_SURE=1`, the default): a clear task
   type and difficulty, a check score far from the pass mark, or a first-choice model well ahead
   of the second. Those decisions are logged as "sure", with no Laya call. `0` asks Laya every
   time (more shadow data, more CPU).
8. **Warm-up.** The first predictions run while Laya loads, even with a fixed time limit, so the
   first question doesn't pay for them.
9. **No surprise download.** The `laya` extra's packages are checked before anything is fetched.
   Without them, the exact install command is shown instead: on Linux without a GPU, PyTorch's
   CPU build first, since the default wheel from PyPI brings about 2.5 GB of GPU libraries. A checkpoint
   already on disk is used with no network check. A first download says what it is and how big
   (about 846 MB for the English checkpoint) before it starts. `tempo-server doctor` has a Laya
   line: off, not installed (with the command), not downloaded yet, or the runner in use, why,
   and the time per kind of decision from the last load (`laya/status.json`).

## Fine-tuned checkpoint: does INT8 hold up once Laya is trained?

No. Laya's own fine-tuned checkpoint (`typed-decisions`) was run on its held-out benchmark (the
first 30 cases of each of its 4 workflows: 120 cases, 600 decisions, scored against the gold
labels). Full precision reproduces Laya's published accuracy; INT8 loses 8 points:

| Backend (4 threads) | Accuracy | Choice | Yes/no | Score | Per case (median / p95) |
|---|---|---|---|---|---|
| PyTorch fp32 | **0.767** (published: 0.766) | 0.728 | 0.906 | 0.692 | 4.9 / 7.1 s |
| ONNX INT8 | 0.683 | 0.744 | 0.761 | 0.579 | 3.7 / 5.8 s |

The two agree on 75% of decisions. On these longer states (about 700 tokens) INT8 is only 1.35×
faster. A fine-tuned model is where Laya's answers start to matter, so INT8 is not worth it.

## End to end: what an answer costs now

Tempo with the offline mock models and real Laya (English checkpoint, PyTorch fp32, 4 threads),
6 questions of different kinds, time per answer:

| Setup | Median | Max | Laya |
|---|---|---|---|
| Laya off | 1,862 ms | 1,983 ms | — |
| Laya in shadow mode (default) | 1,905 ms | 2,017 ms | all 50 predictions logged |
| `should_stop` taken over | 1,834 ms | 2,423 ms | decided 6 of 6 times, waited 747 ms on average |

Load and measuring took 25 s in the background; the measured limit was 3.4 s (1.5× the slowest
group: plan 2.3 s, assess 2.2 s, pick 0.8 s). Before these changes, every decision group waited
up to the limit, which on CPU meant 1.5–2 s per group or losing the decision to the rules.

So on an ordinary 4-core CPU:

- **shadow mode costs nothing**;
- **taking over one decision costs about 0.4–0.8 s per stage**, well inside the measured limit;
- **taking over whole groups** (plan: 4 questions) costs 1.5–2.3 s once per question.

A faster CPU gets a lower limit automatically. `tempo-server laya status` shows the runtime and threads,
and the plan line in the thinking window shows the measured limit.

## "invalid temperatures ... choice:11+=0.10 -> 0.5"

Laya's, not Tempo's. The stock English checkpoint's `rl_agent_config.json` ships
`temperature_by_options["choice:11+"] = 0.1006`, below the 0.5 minimum Laya itself enforces
(`TEMP_MIN` in [`laya/common.py`](https://github.com/NandhaKishorM/laya/blob/main/laya/common.py),
checked 2026-09-29; the value is in the checkpoint's
[`rl_agent_config.json`](https://huggingface.co/convaiinnovations/laya/blob/main/rl_agent_config.json),
checked 2026-09-30), so Laya warns on every load. The bucket
is used only for choices with 11 or more options, and Tempo never asks one (its model shortlist
is capped at 10, and the task-type question has 8). So Tempo logs one plain line instead of the
warning; any other rejected entry still shows as a warning. Tempo's own fine-tunes now fit
temperatures within Laya's range `[0.5, 5]` (they allowed 0.1–10 before). A draft upstream
issue, not posted, is in [upstream/laya-choice-11-temperature.md](./upstream/laya-choice-11-temperature.md).

## Options not taken, and when to revisit

- **Multilingual checkpoint** (`TEMPO_LAYA_CHECKPOINT=multilingual`): 2.6× faster and reads
  100+ languages, but it is a different model. Worth fine-tuning and comparing with
  `tempo-server laya compare` once Tempo has its own data, especially for non-English users.
- **INT8**: revisit if Laya ships quantization-aware training or a calibrated static INT8
  recipe; check it with `tempo-server laya compare` on your own held-out decisions first.
- **bf16 on CPU**: needs AVX-512 BF16 or AMX, which this CPU lacks; on CPUs that have them,
  PyTorch bf16 autocast may be worth measuring (`LAYA_CPU_AMP=bf16`).
