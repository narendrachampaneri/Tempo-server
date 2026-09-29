"""Code and maths answers checked by running them (tempo/execute.py) inside the pipeline: a
failure goes to the fix stage with its error, the thinking window says what ran, and each
result is recorded as a training reward."""

import os

import pytest
from conftest import make_engine, user

from tempo import execute
from tempo.sandbox import Sandbox, sandbox_home

F = "```"
TEST = f"{F}python\ndef test_add():\n    assert add(2, 2) == 4\n{F}"
BUGGY = f"{F}python\ndef add(a, b):\n    return a - b\n{F}\n\n{TEST}"
FIXED = f"{F}python\ndef add(a, b):\n    return a + b\n{F}\n\n{TEST}"


@pytest.fixture(scope="module", autouse=True)
def runtimes():
    box = Sandbox(sandbox_home(None))
    if not box.available("python"):
        if os.environ.get("TEMPO_SANDBOX_REQUIRED") == "1":
            pytest.fail("sandbox runtimes are not installed")
        pytest.skip("sandbox runtimes not installed (tempo-server sandbox install)")


def sandbox_events(result):
    return [e for e in result.events if e.type == "sandbox"]


async def test_failing_code_goes_to_the_fix_stage_with_the_error():
    engine, backend = make_engine(
        {"*:draft": [("answer", BUGGY)], "*:fix": [("answer", FIXED)]}, sandbox="auto"
    )
    result = await engine.complete(user("Write a Python function add(a, b) with a test"))
    runs = sandbox_events(result)
    assert [e.data["status"] for e in runs] == ["failed", "passed"]
    assert "1 of 1 tests failed" in runs[0].text and runs[0].text.startswith("✗")
    assert "1/1 tests passed" in runs[1].text and runs[1].text.startswith("✓")
    fix_prompt = next(m for model, m, purpose in backend.calls if purpose == "fix")
    assert "test_add" in str(fix_prompt) and "AssertionError" in str(fix_prompt)
    assert "a + b" in result.text and result.stop_reason == "passed"
    stage = next(e for e in result.events if e.type == "stage_start" and e.data["job"] == "check")
    assert "sandbox" in stage.text
    rows = engine.store.executions(result.question_id)
    assert [(r["status"], r["reward"], r["tests_total"]) for r in rows] == [
        ("failed", 0.0, 1),
        ("passed", 1.0, 1),
    ]


async def test_a_wrong_calculation_is_fixed():
    engine, _ = make_engine(
        {
            "*:draft": [("answer", "17% of 2,340 is **400**.")],
            "*:fix": [("answer", "It is **397.8**.")],
        },
        sandbox="auto",
    )
    result = await engine.complete(user("What is 17% of 2,340?"))
    runs = sandbox_events(result)
    assert runs[0].data["status"] == "failed" and runs[0].data["method"] == "rules"
    assert "Computed 397.8" in runs[0].text and "says 400" in runs[0].text
    assert runs[-1].data["status"] == "passed" and "397.8" in result.text


async def test_harder_maths_asks_a_model_for_a_program_once():
    program = f"{F}python\nprint((240 + 180) / (3 + 2))\n{F}"
    engine, backend = make_engine(
        {
            "*:draft": [("answer", "The average speed is **84 km/h**.")],
            "*:compute": [("answer", program)],
        },
        sandbox="auto",
    )
    question = (
        "A train travels 240 km in 3 hours, then 180 km in 2 hours. "
        "What is its average speed in km/h?"
    )
    result = await engine.complete(user(question))
    runs = sandbox_events(result)
    assert runs and runs[0].data["status"] == "passed" and runs[0].data["method"] == "program"
    assert sum(1 for _, _, purpose in backend.calls if purpose == "compute") == 1


async def test_rules_only_never_asks_a_model():
    engine, backend = make_engine(sandbox="auto", sandbox_math="rules")
    await engine.complete(user("A shop sells 3 pens for 45 rupees. How much do 7 pens cost?"))
    assert not [p for _, _, p in backend.calls if p == "compute"]


async def test_not_installed_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPO_SANDBOX_HOME", str(tmp_path))
    engine, _ = make_engine({"*:draft": [("answer", BUGGY)]}, sandbox="auto")
    result = await engine.complete(user("Write a Python function add(a, b) with a test"))
    assert not sandbox_events(result)
    notes = [e.text for e in result.events if e.type == "note"]
    assert any(
        "sandbox is not installed" in n and "tempo-server sandbox install" in n for n in notes
    )


async def test_off_means_nothing_runs():
    engine, _ = make_engine({"*:draft": [("answer", BUGGY)]}, sandbox="off")
    assert engine.sandbox is None
    result = await engine.complete(user("Write a Python function add(a, b) with a test"))
    assert not sandbox_events(result)


def test_program_building_and_parsing():
    program = execute.build_program("Write add", BUGGY)
    assert program.language == "python" and program.has_tests
    assert execute.build_program("hi", "No code here.") is None
    assert execute.build_program("", f"{F}bash\nls -la\n{F}") is None
    js = execute.build_program("", f"{F}js\nconsole.log(1)\n{F}")
    assert js.language == "javascript" and not js.has_tests
    tests_import = (
        f"{F}python\nfrom mathutil import double\n\ndef test_d():\n    assert double(2) == 4\n{F}"
    )
    program = execute.build_program(
        "", f"{F}python\ndef double(x):\n    return 2 * x\n{F}\n{tests_import}"
    )
    assert "mathutil.py" in program.files and "def double" in program.files["mathutil.py"]


@pytest.mark.parametrize(
    "question,expression",
    [
        ("What is 17% of 2,340?", "(17) / 100 * (2340)"),
        ("Calculate (3.5 + 2) * 4", "(3.5 + 2) * 4"),
        ("What's 12 times 12?", "12 * 12"),
        ("What is the square root of 144?", "sqrt(144)"),
        ("What is 2 to the power of 10?", "2 ** 10"),
        ("Explain TCP vs UDP", None),
        ("What is the capital of France?", None),
        ("What is 5% of the budget?", None),
    ],
)
def test_math_expressions_from_simple_questions(question, expression):
    assert execute.math_expression(question) == expression


@pytest.mark.parametrize(
    "answer,number",
    [
        ("17% of 2,340 is **397.8**.", 397.8),
        ("So the answer is 1,024.", 1024.0),
        ("x = 3 + 4 = 7", 7.0),
        ("Step 1: 12. Step 2: 30. Total: 42", 42.0),
        ("No numbers", None),
    ],
)
def test_the_answers_final_number(answer, number):
    assert execute.claimed_number(answer) == number


def test_numbers_match_when_rounded_as_shown():
    assert execute.same_number(397.8, 397.8000000001)
    assert execute.same_number(0.33, 1 / 3)
    assert not execute.same_number(400, 397.8)
