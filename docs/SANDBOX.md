# The sandbox: checking code and maths answers by running them

_Step 8, 2026-09-29._ Tempo-server runs code answers (with their tests) and computes maths
answers in a WebAssembly sandbox, so "passed" can mean "the tests pass" and "the number is
right", not only "a judge liked it". It needs no Docker, no GPU and no special hardware, and it
installs with pip on Windows, macOS and Linux.

```bash
tempo-server sandbox install    # once: about 16 MB, checked by SHA-256, then compiled for this computer
tempo-server sandbox status     # on or off, installed runtimes, limits
tempo-server sandbox run f.py   # try it on a file (.py or .js)
tempo-server doctor             # also says whether it is installed
```

`tempo-server setup` offers to install it too. Until it is installed, code and maths answers are
checked as before (heuristics and a judge), and the thinking window says the sandbox is missing.

## What was chosen, and why

| Option | Verdict |
|---|---|
| **Wasmtime** (`pip install wasmtime`, Bytecode Alliance) + **CPython 3.14 for WASI** + **QuickJS-ng for WASI** | **Chosen.** Wasmtime is the reference WebAssembly runtime, maintained by the Bytecode Alliance with monthly releases (49.0.0, 2026-09-21) and a security process. Its Python package has wheels for Windows, macOS and Linux on x86-64 and ARM64, for Python 3.9–3.14. WASI gives a program only what it is handed: no network, no processes, only the folders you open for it. |
| Pyodide | Needs a JavaScript runtime (Node or a browser) to run; not pip-only. |
| MicroPython in WebAssembly (`micropython-wasm`) | Small and fast, but not CPython: much of the standard library, `unittest` and many answers' code would not run. |
| `llm-wasm-sandbox` | Bundles the same kind of binaries, but pins an old Wasmtime (<39), installs generic top-level packages (`sandbox`, `mcp_server`) that could clash with others, and has one maintainer (last release 2025-11). |
| Docker, gVisor, Firecracker, seccomp, `resource` limits | Not allowed (Docker/special setup) or not available on Windows and macOS. |
| `RestrictedPython`, `exec` with filtered globals | Not a security boundary; escapes are well known. |

The interpreters:

| Language | Build | Licence | Source (checked 2026-09-29) |
|---|---|---|---|
| Python | CPython 3.14.5 built for WASI (WASI SDK 24), with its standard library | PSF-2.0 | [brettcannon/cpython-wasi-build](https://github.com/brettcannon/cpython-wasi-build) (Brett Cannon, a CPython core developer who maintains CPython's WASI support; WASI is a tier 2 CPython platform since 3.13) |
| JavaScript | QuickJS-ng 0.17.0, `qjs-wasi.wasm` | MIT | [quickjs-ng/quickjs](https://github.com/quickjs-ng/quickjs) |

`tempo-server sandbox install` downloads exactly these files, refuses anything whose SHA-256
differs from the one in `tempo/sandbox.py`, unpacks them (refusing archive paths that would land
outside its folder) into `<data folder>/sandbox` (or `TEMPO_SANDBOX_HOME`), and compiles them
once for this computer (a few seconds). A compiled copy from another computer is detected and
rebuilt.

## What the code can and can't do

Every run is a **fresh WebAssembly instance** with its **own empty temporary folder**, deleted
afterwards. Nothing carries over between runs.

| Attempt | What happens |
|---|---|
| Read or write files | Only its temporary folder (`/work`, read-write) and, for Python, the standard library (`/lib`, read-only). Anything else, including `/`, `/etc`, `C:\`, `..` and the host's real paths, does not exist for it. |
| Open a network connection | Impossible: the WASI build has no sockets and no DNS (`OSError: Not supported`, no `getaddrinfo`). |
| Start a process (`subprocess`, `os.system`, `fork`, `exec`) | Impossible: "wasi does not support processes". |
| Read environment variables | It sees only `PYTHONHOME` and `PYTHONDONTWRITEBYTECODE`; none of the host's (so no keys). |
| JavaScript modules | None: no `std`, no `os`, no `require`; `console` only (plus small `assert` helpers). |
| Endless loop | Stopped at the time limit (Wasmtime epoch interruption). |
| Huge output | Stopped by a watchdog as soon as the output passes 4× the limit; what is kept is truncated to the limit. |
| Memory bomb | The WebAssembly memory is capped: Python raises `MemoryError`, QuickJS "out of memory". |
| Filling the disk | Stopped by the watchdog when its temporary folder passes the disk limit. |
| Deep recursion | Stopped by Python's recursion limit, the WebAssembly stack limit (a trap), or the time limit. |

Limits (settings): `TEMPO_SANDBOX_TIMEOUT` (default 10 s), `TEMPO_SANDBOX_MEMORY_MB` (256),
`TEMPO_SANDBOX_OUTPUT_KB` (64); the temporary folder may hold 16 MB. At most two runs happen at
once. Each is proven by the safety tests in `tests/test_sandbox.py`, which CI runs on Windows,
macOS and Linux.

What WebAssembly does not protect against: a bug in Wasmtime itself (it is fuzzed continuously
and has a security response process), and using a lot of CPU for up to the time limit.

## How answers are checked

**Code** (`tempo/execute.py`), in the check stage, for code questions or answers with tests:

1. The answer's code blocks in its main language (Python or JavaScript) are put together, with
   any tests from the question and the answer: plain `assert` lines, `test_*` functions, unittest
   classes, and the common pytest helpers (`pytest.raises`, `approx`, `mark.parametrize`)
   through a small stand-in (pytest itself can't run in WASI). Tests that import the solution
   as a module (`from palindrome import is_palindrome`) get that module.
2. It runs in the sandbox. The result is one of:
   - **passed**: the tests passed (or, without tests, it ran without errors);
   - **failed**: a test failed, it raised an error, or with tests it hit the time, output or disk
     limit. The check fails, and the error ("Failing tests: test_add: AssertionError…") goes to
     the fix stage, whose answer is run again;
   - **inconclusive**: it can't run here (a third-party library such as numpy, keyboard input,
     the network), or without tests it ran into a limit (a server that runs forever). Never
     counted as a failure.

**Maths**, in the check stage, for maths questions:

1. Simple arithmetic ("What is 17% of 2,340?", "Calculate (3.5 + 2) * 4", "square root of 144")
   becomes an expression by rules: no model call.
2. Otherwise (`TEMPO_SANDBOX_MATH=auto`, the default) a model writes a short program that prints
   the result, once per question, costing one free request; `rules` never asks, `off` skips maths.
3. The program runs in the sandbox, and the result is compared with the answer's final number
   (the bold one, else after "=" or "answer is", else the last), allowing for rounding as shown.
   A mismatch found by rules fails the check; one found by a model-written program lowers the
   score strongly (the program could be the wrong one), and both tell the fix stage the computed
   value.

**Thinking window:** the check stage says "+ sandbox", and each run gets a line:
`✓ Ran the python code: 3/3 tests passed (0.8s)`,
`✗ Ran the python code: 1 of 2 tests failed (0.9s)`,
`✓ Computed 397.8 in the sandbox (rules): matches the answer`, or a note when the sandbox is
not installed.

**Recorded for training:** every run is saved (table `executions`: kind, language, status,
tests passed and run, reward, error, time). `export-sft` rows carry it as `execution`, and
`export-pairs` as `chosen_execution` / `rejected_execution`, with a `reward` (tests passed / tests
run, 1 or 0 otherwise, null when inconclusive) for reinforcement learning later.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `TEMPO_SANDBOX` | `auto` | `auto`: run code and maths answers when the sandbox is installed; `off`: never |
| `TEMPO_SANDBOX_MATH` | `auto` | `auto`: rules, else a model-written program; `rules`: no extra model call; `off` |
| `TEMPO_SANDBOX_TIMEOUT` | `10` | Seconds per run |
| `TEMPO_SANDBOX_MEMORY_MB` | `256` | WebAssembly memory per run |
| `TEMPO_SANDBOX_OUTPUT_KB` | `64` | Output kept per run (the run stops at 4× this) |
| `TEMPO_SANDBOX_HOME` | `<data folder>/sandbox` | Where the runtimes live |

## Limits of the checks

- Only Python and JavaScript. Other languages are checked as before.
- Only the standard library (Python) and plain ECMAScript (JavaScript): code using third-party
  packages or Node modules is inconclusive.
- Without tests, "passed" only means the code ran without errors.
- Python is CPython 3.14 on 32-bit WebAssembly: code that needs threads, sockets, processes or
  more than 4 GB of memory can't run.
- Each Python run starts a new interpreter (about 0.1–0.8 s); a question's check stage adds that
  per answer.
