"""Check code and maths answers by running them in the sandbox (tempo/sandbox.py).

Code: the answer's Python or JavaScript code blocks run together with any tests in the question
or the answer (plain ``assert`` lines, ``test_*`` functions, unittest classes, and the common
pytest helpers through a small stand-in). A failure is a failed check whose error message goes
to the fix stage. Code that can't run here (a third-party library, keyboard input, network) is
"inconclusive", never a failure.

Maths: simple arithmetic questions are turned into an expression by rules; otherwise a model
writes a short program (the pipeline asks for it). The program runs in the sandbox and its
result is compared with the answer's final number.

Every result is an ``Execution``, logged per answer so exports can use it as a reward.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Literal

from tempo.sandbox import Language, Limits, RunResult, Sandbox

Status = Literal["passed", "failed", "inconclusive"]

FENCE = re.compile(r"```([\w+#.-]*)[^\n]*\n(.*?)```", re.DOTALL)
PYTHON_TAGS = {"python", "py", "python3", "py3"}
JS_TAGS = {"javascript", "js", "node", "nodejs", "mjs", "cjs"}
NOT_CODE = {"bash", "sh", "shell", "console", "text", "txt", "output", "plaintext", "json", "yaml"}
MARKER = "__TEMPO_TESTS__"


@dataclass
class Execution:
    kind: Literal["code", "math"]
    language: str
    status: Status
    detail: str  # one line for the thinking window
    error: str | None = None  # what the fix stage is told
    tests_total: int = 0
    tests_passed: int = 0
    duration_ms: int = 0
    stopped: str | None = None
    method: str | None = None  # maths: "rules" or "program"
    computed: str | None = None
    claimed: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def reward(self) -> float | None:
        """For training exports: 1 passed, 0 failed, None when nothing could be checked."""
        if self.status == "inconclusive":
            return None
        if self.kind == "code" and self.tests_total:
            return round(self.tests_passed / self.tests_total, 3)
        return 1.0 if self.status == "passed" else 0.0


# --- finding the code --------------------------------------------------------------------


def _looks_python(code: str) -> bool:
    return bool(re.search(r"^\s*(def |class |import |from \w+ import |print\()", code, re.M))


def _looks_js(code: str) -> bool:
    return bool(re.search(r"\b(function\s+\w+|const |let |=>|console\.log)", code))


def code_blocks(text: str) -> list[tuple[Language, str]]:
    blocks: list[tuple[Language, str]] = []
    for tag, code in FENCE.findall(text or ""):
        tag = tag.lower()
        if tag in NOT_CODE:
            continue
        if tag in PYTHON_TAGS or (not tag and _looks_python(code)):
            blocks.append(("python", code))
        elif tag in JS_TAGS or (not tag and _looks_js(code)):
            blocks.append(("javascript", code))
    return blocks


_TESTY = re.compile(r"^\s*(assert\b|def test_|class \w+\(.*TestCase\)|@pytest)", re.M)
_IMPORT_FROM = re.compile(r"^\s*from\s+([A-Za-z_]\w*)\s+import\s", re.M)


@dataclass
class Program:
    language: Language
    code: str
    files: dict[str, str]
    has_tests: bool


def build_program(question: str, answer: str) -> Program | None:
    """The answer's code (in its main language) plus tests from the question and answer."""
    blocks = code_blocks(answer)
    if not blocks:
        return None
    by_lang: dict[Language, int] = {}
    for lang, code in blocks:
        by_lang[lang] = by_lang.get(lang, 0) + len(code)
    language = max(by_lang, key=lambda k: by_lang[k])
    ours = [code for lang, code in blocks if lang == language]
    question_tests = [
        code for lang, code in code_blocks(question) if lang == language and _TESTY.search(code)
    ]
    if language == "javascript":
        body = "\n\n".join([*ours, *question_tests])
        has_tests = bool(re.search(r"\b(assert|console\.assert|expect)\s*\(", body))
        return Program("javascript", JS_PRELUDE + body, {}, has_tests)
    solution = [c for c in ours if not _TESTY.search(c)] or ours[:1]
    tests = [c for c in ours if _TESTY.search(c) and c not in solution] + question_tests
    # Tests that import the solution as a module ("from palindrome import is_palindrome"):
    # give them that module.
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    files = {}
    for block in tests:
        for module in _IMPORT_FROM.findall(block):
            if module not in stdlib and module not in ("pytest", "__future__"):
                files[f"{module}.py"] = "\n\n".join(solution)
    files["pytest.py"] = PYTEST_SHIM
    code = "\n\n".join([*solution, *tests]) + "\n" + PY_HARNESS
    has_tests = bool(tests) or any(_TESTY.search(c) for c in solution)
    return Program("python", code, files, has_tests)


# --- running it ----------------------------------------------------------------------------

PYTEST_SHIM = '''"""A tiny stand-in for the pytest helpers answers use (pytest itself can't run here)."""
import math as _math


class _Raises:
    def __init__(self, expected, match=None):
        self.expected, self.match = expected, match

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError(f"DID NOT RAISE {self.expected}")
        if not issubclass(kind, self.expected):
            return False
        if self.match is not None:
            import re
            assert re.search(self.match, str(value)), f"{value!r} does not match {self.match!r}"
        return True


def raises(expected, *args, match=None, **kwargs):
    if args:
        with _Raises(expected, match):
            args[0](*args[1:], **kwargs)
        return None
    return _Raises(expected, match)


class approx:
    def __init__(self, expected, rel=1e-6, abs=1e-12):
        self.expected, self.rel, self.abs = expected, rel, abs

    def __eq__(self, other):
        try:
            return all(
                _math.isclose(a, b, rel_tol=self.rel, abs_tol=self.abs)
                for a, b in zip(other, self.expected)
            ) and len(other) == len(self.expected)
        except TypeError:
            return _math.isclose(other, self.expected, rel_tol=self.rel, abs_tol=self.abs)

    def __repr__(self):
        return f"approx({self.expected!r})"


class _Mark:
    def parametrize(self, names, values, **_kwargs):
        def decorate(fn):
            fn._tempo_params = ([n.strip() for n in names.split(",")] if isinstance(names, str) else list(names), list(values))
            return fn
        return decorate

    def __getattr__(self, _name):
        return lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda fn: fn))


mark = _Mark()


def fixture(*args, **kwargs):
    return args[0] if args and callable(args[0]) else (lambda fn: fn)
'''

PY_HARNESS = f'''
def _tempo_run_tests():
    import inspect, json, sys, unittest
    passed, failed = 0, []
    for name, fn in list(globals().items()):
        if not (name.startswith("test") and inspect.isfunction(fn)):
            continue
        params = getattr(fn, "_tempo_params", None)
        cases = [dict(zip(params[0], v if isinstance(v, (tuple, list)) else (v,))) for v in params[1]] if params else [{{}}]
        for case in cases:
            if len(inspect.signature(fn).parameters) != len(case):
                continue
            try:
                fn(**case)
                passed += 1
            except Exception as exc:
                failed.append(f"{{name}}({{', '.join(map(repr, case.values()))}}): {{type(exc).__name__}}: {{exc}}")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if suite.countTestCases():
        result = unittest.TestResult()
        suite.run(result)
        for test, trace in result.failures + result.errors:
            failed.append(f"{{test.id().split('.')[-1]}}: {{trace.strip().splitlines()[-1]}}")
        passed += result.testsRun - len(result.failures) - len(result.errors)
    print("{MARKER}", json.dumps({{"passed": passed, "failed": failed}}))
    if failed:
        raise SystemExit(1)


_tempo_run_tests()
'''

JS_PRELUDE = """// Tempo sandbox prelude: assert helpers (QuickJS has no Node modules)
function assert(ok, message) { if (!ok) throw new Error(message || "assertion failed"); }
assert.equal = (a, b, m) => assert(a == b, m || `${a} != ${b}`);
assert.strictEqual = (a, b, m) => assert(a === b, m || `${a} !== ${b}`);
assert.deepStrictEqual = (a, b, m) => assert(JSON.stringify(a) === JSON.stringify(b), m || `${JSON.stringify(a)} !== ${JSON.stringify(b)}`);
assert.ok = assert;
console.assert = (ok, ...m) => assert(ok, m.join(" ") || "console.assert failed");
"""

# Errors that mean "this can't run here", not "this is wrong".
_CANT_RUN = re.compile(
    r"(ModuleNotFoundError|No module named|EOFError|wasi does not support|"
    r"\[Errno 58\] Not supported|could not load module|has no attribute 'getaddrinfo'|"
    r"ReferenceError: (require|process|window|document) is not defined)"
)


def _last_error(stderr: str) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    message = lines[-1].strip()
    if message.startswith("at "):  # a JavaScript stack: the error is above it
        message = next((ln.strip() for ln in lines if re.match(r"\s*\w*Error\b", ln)), message)
        prelude = JS_PRELUDE.count("\n")
        lines_at = [int(n) - prelude for n in re.findall(r"main\.js:(\d+)", stderr)]
        line = next((n for n in lines_at if n > 0), None)
        return (message + (f" (line {line})" if line else ""))[:400]
    where = next((ln.strip() for ln in reversed(lines) if 'File "/work/' in ln), "")
    if where:
        line = re.search(r"line (\d+)", where)
        message += f" (line {line.group(1)})" if line else ""
    return message[:400]


def run_code(sandbox: Sandbox, program: Program, limits: Limits | None = None) -> Execution:
    result = sandbox.run(program.language, program.code, program.files, limits)
    return interpret(program, result)


def interpret(program: Program, result: RunResult) -> Execution:
    lang = program.language
    tests = _tests(result.stdout)
    base = {"kind": "code", "language": lang, "duration_ms": result.duration_ms}
    secs = f"{result.duration_ms / 1000:.1f}s"
    if result.stopped:
        what = result.summary
        if program.has_tests or result.stopped == "trap":
            reason = {
                "time": "The code did not finish within the time limit (an endless loop?)",
                "output": "The code printed far more output than expected",
                "disk": "The code wrote far more data to files than expected",
                "trap": "The code crashed the interpreter (too deep recursion or too much memory)",
            }[result.stopped]
            return Execution(
                status="failed",
                detail=f"Ran the {lang} code: {what} ({secs})",
                error=f"{reason} when run with its tests.",
                stopped=result.stopped,
                **base,
            )
        return Execution(
            status="inconclusive",
            detail=f"Ran the {lang} code: {what}, no tests to judge it by",
            stopped=result.stopped,
            **base,
        )
    if tests is not None:
        passed, failed = tests["passed"], tests["failed"]
        total = passed + len(failed)
        if not failed:
            return Execution(
                status="passed",
                detail=f"Ran the {lang} code: {passed}/{total} tests passed ({secs})",
                tests_total=total,
                tests_passed=passed,
                **base,
            )
        return Execution(
            status="failed",
            detail=f"Ran the {lang} code: {len(failed)} of {total} tests failed ({secs})",
            error="Failing tests: " + "; ".join(failed[:3]),
            tests_total=total,
            tests_passed=passed,
            **base,
        )
    if result.ok:
        tail = "tests passed" if program.has_tests else "ran without errors (no tests)"
        return Execution(status="passed", detail=f"Ran the {lang} code: {tail} ({secs})", **base)
    error = _last_error(result.stderr) or result.summary
    if _CANT_RUN.search(result.stderr):
        return Execution(
            status="inconclusive",
            detail=f"Couldn't run the {lang} code here: {error}",
            error=error,
            **base,
        )
    return Execution(
        status="failed",
        detail=f"Ran the {lang} code: it failed with {error}",
        error=f"Running the code failed: {error}",
        **base,
    )


def _tests(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(MARKER):
            try:
                data = json.loads(line[len(MARKER) :])
            except ValueError:
                return None
            if data["passed"] or data["failed"]:
                return data
            return None
    return None


# --- maths ---------------------------------------------------------------------------------

_WORDS = [
    (r"\bmultiplied by\b|\btimes\b|×|\bx\b(?=\s*\d)", "*"),
    (r"\bdivided by\b|\bover\b|÷", "/"),
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\bsquared\b", "**2"),
    (r"\bcubed\b", "**3"),
    (r"\bto the power of\b|\^", "**"),
    (r"\bsquare root of\b|\bsqrt\b|√", "sqrt"),
    (r"\bmod(?:ulo)?\b", "%"),
]
_ASK = re.compile(
    r"^\s*(?:what\s+is|what's|calculate|compute|evaluate|work\s+out|find)\s+(?:the\s+value\s+of\s+)?(.+?)\s*[?.!]*\s*$",
    re.I,
)
_PERCENT_OF = re.compile(r"^([\d.,]+)\s*%\s*of\s*([\d.,]+)$", re.I)
_EXPR_OK = re.compile(r"^[\d\s.+\-*/()%]*(sqrt\([\d\s.+\-*/()%]+\)[\d\s.+\-*/()%]*)*$")


def math_expression(question: str) -> str | None:
    """A Python expression for simple arithmetic questions ("What is 17% of 2,340?",
    "Calculate (3.5 + 2) * 4"), or None."""
    match = _ASK.match(question.strip().splitlines()[0] if question.strip() else "")
    if not match:
        return None
    text = re.sub(r"^the\s+", "", match.group(1).strip(), flags=re.I)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # thousands separators
    percent = _PERCENT_OF.match(text)
    if percent:
        a, b = (x.replace(",", "") for x in percent.groups())
        return f"({a}) / 100 * ({b})"
    for pattern, repl in _WORDS:
        text = re.sub(pattern, f" {repl} ", text, flags=re.I)
    text = re.sub(r"sqrt\s+([\d.]+)", r"sqrt(\1)", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not re.search(r"\d", text) or not re.search(r"[+\-*/%]|sqrt", text):
        return None
    if not _EXPR_OK.match(text) or re.search(r"\d\s*%(?!\s*\d)", text):
        return None
    return text


def math_program(expression: str) -> str:
    return f"from math import sqrt\nvalue = ({expression})\nprint(repr(float(value)))\n"


_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"


def claimed_number(answer: str) -> float | None:
    """The answer's final number: a bold number, else one after '=' or 'answer is', else the
    last number in the text."""
    text = re.sub(r"```.*?```", " ", answer or "", flags=re.S)
    for pattern in (
        rf"\*\*[^*\d-]*({_NUMBER})[^*]*\*\*",
        rf"(?:answer|result|total)\s*(?:is|=|:)\s*[^\d-]{{0,6}}({_NUMBER})",
        rf"=\s*[^\d-]{{0,6}}({_NUMBER})",
        rf"({_NUMBER})",
    ):
        found = re.findall(pattern, text, re.I)
        if found:
            try:
                return float(found[-1].replace(",", ""))
            except ValueError:
                continue
    return None


def _decimals(value: str) -> int:
    return len(value.split(".", 1)[1]) if "." in value else 0


def same_number(claimed: float, computed: float) -> bool:
    if math.isclose(claimed, computed, rel_tol=1e-6, abs_tol=1e-9):
        return True
    places = _decimals(repr(claimed))
    return places <= 6 and round(computed, places) == claimed


def check_math(
    sandbox: Sandbox,
    answer: str,
    program: str,
    method: str,
    limits: Limits | None = None,
) -> Execution:
    """Run a program that prints the answer to a maths question and compare."""
    result = sandbox.run("python", program, limits=limits)
    base = {
        "kind": "math",
        "language": "python",
        "method": method,
        "duration_ms": result.duration_ms,
    }
    if not result.ok:
        error = _last_error(result.stderr) or result.summary
        return Execution(
            status="inconclusive",
            detail=f"Couldn't compute the result in the sandbox: {error}",
            stopped=result.stopped,
            **base,
        )
    printed = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    try:
        computed = float(printed[-1].strip().replace(",", "")) if printed else None
    except ValueError:
        computed = None
    claimed = claimed_number(answer)
    if computed is None or claimed is None or not math.isfinite(computed):
        return Execution(
            status="inconclusive",
            detail="Computed in the sandbox, but found no number to compare",
            computed=None if computed is None else f"{computed:g}",
            claimed=None if claimed is None else f"{claimed:g}",
            **base,
        )
    shown = f"{computed:.10g}"
    if same_number(claimed, computed):
        return Execution(
            status="passed",
            detail=f"Computed {shown} in the sandbox ({method}): matches the answer",
            computed=shown,
            claimed=f"{claimed:g}",
            **base,
        )
    return Execution(
        status="failed",
        detail=f"Computed {shown} in the sandbox ({method}), but the answer says {claimed:g}",
        error=f"The result computed by running the calculation is {shown}, but the answer "
        f"says {claimed:g}. Recheck the working.",
        computed=shown,
        claimed=f"{claimed:g}",
        **base,
    )


PROGRAM_PROMPT = """Write a short Python program that computes the final numeric answer to the
question below. Use only the Python standard library. Print only the final number (no words,
no units) on the last line. Reply with one ```python code block and nothing else.

Question:
{question}"""


def program_from_reply(reply: str) -> str | None:
    blocks = [code for lang, code in code_blocks(reply) if lang == "python"]
    return blocks[0] if blocks else None
