# Draft issue for Laya (not posted)

_For the owner to post, if they agree, at https://github.com/NandhaKishorM/laya/issues. Drafted
2026-09-29 from Tempo-server's step 10 laptop test. Nothing here has been sent anywhere._

---

**Title:** Stock English checkpoint ships `temperature_by_options["choice:11+"] = 0.1006`, which
laya itself rejects (RuntimeWarning on every load)

**What happens**

Loading the stock English checkpoint with laya 0.3.21 prints, every time:

```
RuntimeWarning: laya: this checkpoint ships invalid temperatures or values outside [0.5, 5];
using choice:11+=0.10058280825614929 -> 0.5. Treat confidence from the affected entries as
uncalibrated.
```

**Why**

`rl_agent_config.json` in `convaiinnovations/laya` (main, checked 2026-09-29) has:

```json
"temperature_by_options": {
  "choice:3-5": 1.7601518630981445,
  "choice:6-10": 1.0000158548355103,
  "score:3-5": 1.2514300346374512,
  "noul:2": 1.983399510383606,
  "choice:11+": 0.10058280825614929,
  "choice:2": 1.9063563346862793
}
```

`laya/common.py` sets `TEMP_MIN = 0.5` and `TEMP_MAX = 5.0`, and `clamp_temperature()` confines
every fitted temperature to that range (the comment there explains why: a temperature far
below 1 sharpens the logits and overstates confidence, and even quotes this 0.1006 value). So
the library correctly refuses the value its own published checkpoint ships.

**Suggested fix**

Either re-fit `choice:11+` on enough held-out rows with 11 or more options (0.10 suggests very
few rows in that bucket), or drop the key so the per-type temperature for choices applies;
and, going forward, clamp to `[TEMP_MIN, TEMP_MAX]` when the calibration notebook writes
`temperature_by_options`, so a checkpoint can't ship a value the loader rejects.

**Impact**

Only choice questions with 11 or more options use this bucket; their confidence is
uncalibrated until it is re-fitted. Applications that never ask such questions are not
affected, but every load shows a RuntimeWarning that looks like a problem with the install.

**Environment**

laya 0.3.21 (PyPI), stock English checkpoint from the Hugging Face Hub, CPU, Python 3.11–3.14.

---

## What Tempo-server does meanwhile

- Tempo never asks a choice with 11 or more options (its model shortlist is capped at 10; the
  task-type question has 8), so the clamped bucket changes nothing for Tempo. When this is the
  only rejected entry, Tempo logs one plain line instead of the warning
  (`tempo/laya_runtime.py`, `explain_temperature_warning`); any other rejected entry still
  shows as a warning.
- Tempo's own Laya fine-tunes (`tempo/trainkit.py`) now fit temperatures within Laya's
  `[0.5, 5]` (they allowed 0.1 to 10 before, which could have caused the same warning), and
  they don't write `temperature_by_options`.
