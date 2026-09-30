# Speed: fast answers within the time budget

How Tempo answers quickly without cutting answers short, and how it was measured. Step 10 of
[STATUS.md](./STATUS.md); the laptop test that started it had Groq, NVIDIA and Google keys, a
60 s budget, and three problems: Best mode waited for the slowest model, nemotron-3-super (the
strongest model on paper) took 43 s of the 60, and a Gemini 2.5 model was "not found".

## What happens now

**The first good answer is shown at once.** The answer that streams is marked ready
(`answer_ready`) the moment its own model finishes and passes the quick checks (not empty, not
cut off, not a refusal, the right language), even while other models of the same stage are still
writing. Checking goes on in the background; the web page shows "Checking in the background…".
The answer on screen stays unless a check finds a real problem with it. Then the fix replaces it
as a revision (`answer_revised`): the page shows what the check found and a line-by-line
difference. A tie or an unchecked alternative never replaces it.

**Models are picked by measured speed** (`tempo/speed.py`). Every call records its time to the
first token and its tokens a second. A call stopped at the time limit records what it measured
too, or a model that always runs past the limit would never look slow. The figures are the median
of the last 20 calls, blended with the registry's figures (worth one call). The blend is in
seconds per token, so one 43 s call is enough for a model that looks fast on paper to look slow.
At start-up the server reads the last 14 days of calls from its log. The router weighs speed by
mode. Fast weighs it most. Auto weighs it more for simple questions and less for hard ones. Best
weighs it least.

**Stages are planned to fit the budget.** The expected answer length comes from the question (a
full web page ~3,500 tokens, an essay ~1,500, "in one line" ~60, "300 words" ~420). Each kind of
stage gets a time estimate from measured speed, counting a reasoning model's hidden thinking and
continuations past a model's output limit. The plan keeps only the stages that fit (the `plan`
event shows the estimates). A stage starts only if a model can finish it in the time left; a model
that can't is skipped ("can't finish in the time left"). If nothing can finish and there is no
answer yet, the fastest model still tries (better than no answer). In Best mode, a parallel draft
whose measured time would crowd out the check and merge after it is left out, with a note, as
long as three families still draft.

**The budget is a ceiling, and it never cuts an answer.**

- When the time is up, no new stage starts.
- An answer already arriving is finished, for at most `TEMPO_FINISH_GRACE` seconds more (120).
  After that, what arrived is kept and marked as cut.
- Parallel drafts that can't change the answer on screen are stopped. With no stage left to
  check or merge them, they couldn't.
- If the budget ends with an answer in hand, it is returned with a plain note ("Stopped by the
  60s time budget after 2 stages: this is the best answer so far (not checked yet)"). Before
  step 10, the same case said "using the best answer so far" and then "No answer was produced
  within the budget".

**Long answers are finished.** When a model stops at its output limit (`finish_reason` "length"),
Tempo asks the same model to continue, or the next one in the slot, up to 3 times. It joins the
pieces without repeating the overlap, and says so in the thinking window (`continue` events).

**Models the provider no longer offers are skipped.** Before routing, each provider the caller
can use has its model list read, if it wasn't read in the last 24 hours: once per run, and
the question waits at most 6 s (a list that can't be read never stops the answer). Seeds the list lacks are skipped with a note. A "model not
found" error marks the model as not offered, so no later question wastes an attempt on it.
Both are saved in `catalog.json`.

**Warm-up.** At start-up the server asks Ollama to load the local model that would answer first
(`keep_alive` 15 minutes), and Laya runs its first predictions while loading. See
[LAYA_CPU.md](./LAYA_CPU.md) for Laya's faster runner and when it isn't asked at all.

## Measuring it

`tempo-server bench` asks a few standard questions in each mode. It prints the median seconds to
the first token, to the answer being ready, and to every stage done, plus errors and the models
used:

```bash
tempo-server bench                          # auto, fast and best; 3 questions each
tempo-server bench --modes best -n 2 --repeat 3 --time-budget 30 --json
```

Bench questions are not logged and skip the cache; their timings update the speed record. It
needs keys (or `TEMPO_ENABLE_MOCK=1` for the offline demo models). The speed logic itself is
tested with fake providers that are fast, slow or failing (`tests/test_speed.py`,
`tests/test_bench.py`, `tests/test_pipeline.py`).

## Before and after, with fake providers

[`scripts/speed_compare.py`](../scripts/speed_compare.py) replays the laptop test with fake
providers and the laptop's timings:

| Fake model | First token | Done |
|---|---|---|
| groq llama-3.3-70b | 0.3 s | 1.5 s |
| groq gpt-oss-120b (reasoning) | 0.5 s | 2.5 s |
| nvidia nemotron-3-super (strongest and fast on paper) | 6 s | 43 s |
| gemini-3-flash | 1.5 s | 6 s |
| gemini 2.5 flash and pro | "model not found" | |

Each mode answers three questions (easy, code, explain) three times with one engine. A virtual
clock runs it in under a second; no network, no keys. The "before" column is main before step 10
(`ec764cc`), and the "after" column is step 10. All times are medians in seconds.

"On screen" means a complete answer is shown. Before step 10 that was the final answer, since
there was no `answer_ready`.

Time budget 60 s (the default):

| Mode | First token | On screen | On screen, slowest | Done | Done, slowest | Answered | "Not found" calls |
|---|---|---|---|---|---|---|---|
| Fast, before | 1.5 | 6.0 | 45.2 | 6.0 | 45.2 | 9/9 | 1 |
| Fast, after | **0.3** | **1.5** | 43.0 | **2.3** | 43.8 | 9/9 | **0** |
| Auto, before | 6.0 | 45.2 | 45.4 | 45.2 | 45.4 | 9/9 | 1 |
| Auto, after | **0.5** | **2.5** | 43.0 | **3.0** | 43.5 | 9/9 | **0** |
| Best, before | 0.5 | 60.0 | 60.0 | 60.0 | 60.0 | 9/9 | 2 |
| Best, after | **0.3** | **1.5** | **2.5** | **18.5** | **56.0** | 9/9 | **0** |

Time budget 30 s, Best mode (the laptop's failure: the slow draft is still running when the
budget ends):

| | On screen | Done | Done, slowest | Answered |
|---|---|---|---|---|
| Before | none | 30.0 | 30.0 | **0/9**, each "No answer was produced within the budget" |
| After | **1.5** | **18.5** | 30.0 | **9/9** |

In Fast and Auto, the 43 s "slowest" is the first question sent to nemotron, before anything was
measured (on paper it is fast). From then on it is not chosen while a faster model fits. No run
went past its budget, before or after.

Reproduce:

```bash
python scripts/speed_compare.py                       # this checkout
python scripts/speed_compare.py --budget 30 --modes best
git worktree add ../tempo-old ec764cc                 # main before step 10
PYTHONPATH=../tempo-old python scripts/speed_compare.py
```

These are fake providers with fixed timings: real ones vary from call to call, and the free
tiers' rate limits add waits the fakes don't have. `tempo-server bench` with real keys is the
real measure (**needs key**; STATUS.md lists what to run).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `TEMPO_TIME_BUDGET` | `60` | Seconds per question: no new stage starts after it |
| `TEMPO_FINISH_GRACE` | `120` | Extra seconds an answer already arriving may take to finish after the budget |
| `TEMPO_LAYA_BACKEND` | `auto` | Laya's runner; `auto` times PyTorch and ONNX Runtime fp32 once and keeps the faster ([LAYA_CPU.md](./LAYA_CPU.md)) |
| `TEMPO_LAYA_SKIP_SURE` | `1` | Don't ask Laya when the rules are sure (`0` asks it every time) |
