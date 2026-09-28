"""Answer checking: is this answer good enough to stop, or does another stage need to run?

Cheap heuristics run on every answer (empty, cut off, refusal, broken JSON, Python syntax,
an answer mostly in another language, repetition). An LLM judge from a different model
family can add a 0-10 grade.
Heuristic hard failures always win over a judge's grade. Code is syntax-checked, never run.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tempo.language import mismatch as language_mismatch
from tempo.types import QueryProfile

PASS_THRESHOLD = {"fast": 0.6, "auto": 0.7, "private": 0.7, "best": 0.85}
# Without a judge, heuristic scores are scaled down: a clean answer passes "auto" but never
# "best", and two soft issues are enough to fail "auto".
NO_JUDGE_CAP = 0.8
HARD_FAIL_CAP = 0.2
SOFT_PENALTY = 0.1

_REFUSAL = re.compile(
    r"^\s*(i'?m sorry,? but |sorry,? )?"
    r"(i (can(no|')t|am unable to|won'?t) (help|assist|provide|do that|comply)"
    r"|as an ai(?: language model)?,? i (can(no|')t|am unable))",
    re.IGNORECASE,
)
# The user clearly asked for code (not just a question about code).
_CODE_ASK = re.compile(
    r"\b(write|writing|written|fix|fixing|fixed|implement\w*|convert\w*)\b"
    r"|लिखो|लिखिए|लिखें|ठीक कर|बदलो|લખો|લખી|સુધારો|બદલો",
    re.IGNORECASE,
)
_FENCE = re.compile(r"```([\w+-]*)\n(.*?)```", re.DOTALL)


@dataclass
class CheckResult:
    score: float
    passed: bool
    issues: list[str] = field(default_factory=list)
    hard_fail: bool = False
    judge_score: float | None = None  # 0-10 as the judge gave it
    judge_model: str | None = None
    heuristic_score: float | None = None  # what the heuristics alone would have scored

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "passed": self.passed,
            "issues": self.issues,
            "hard_fail": self.hard_fail,
            "judge_score": self.judge_score,
            "judge_model": self.judge_model,
            "heuristic_score": self.heuristic_score,
        }


@dataclass
class Heuristics:
    issues: list[str] = field(default_factory=list)
    hard_fail: bool = False
    soft: float = 0.0  # soft issues, weighted

    def fail(self, issue: str) -> None:
        self.issues.append(issue)
        self.hard_fail = True

    def warn(self, issue: str, weight: float = 1.0) -> None:
        self.issues.append(issue)
        self.soft += weight

    @property
    def score(self) -> float:
        base = HARD_FAIL_CAP if self.hard_fail else 1.0
        return max(0.0, base - SOFT_PENALTY * self.soft)


def extract_json(text: str) -> Any:
    """First JSON value in the text (fenced or bare). Raises ValueError if none parses."""
    for match in _FENCE.finditer(text):
        if match.group(1).lower() in ("json", ""):
            try:
                return json.loads(match.group(2))
            except ValueError:
                pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except ValueError:
                continue
    raise ValueError("no JSON found")


def run_heuristics(
    profile: QueryProfile,
    question: str,
    answer: str,
    finish_reason: str | None = None,
    structured: bool = False,
) -> Heuristics:
    """``structured``: the answer is JSON the caller asked for (response_format), already
    validated, so prose checks (language, code blocks, length) don't apply."""
    h = Heuristics()
    text = answer.strip()
    if not text:
        h.fail("empty answer")
        return h
    if finish_reason == "length":
        h.fail("cut off at the length limit")
    if answer.count("```") % 2:
        h.fail("unclosed code block")
    if _REFUSAL.match(text):
        h.fail("refused to answer")

    if "json" in profile.needs:
        try:
            extract_json(text)
        except ValueError:
            h.fail("asked for JSON but the answer has no valid JSON")

    if structured:
        return h
    if profile.task == "code":
        blocks = [(lang.lower(), code) for lang, code in _FENCE.findall(answer)]
        if not blocks and _CODE_ASK.search(question):
            # Only when the user clearly asked for code; otherwise the judge decides.
            h.fail("asked for code but the answer has no code block")
        for lang, code in blocks:
            if lang in ("python", "py", "python3"):
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    h.fail(f"Python syntax error on line {exc.lineno}: {exc.msg}")
                    break

    if len(text) < 40 and profile.est_output_tokens >= 300 and profile.task != "chat":
        h.warn("very short for this question")

    wrong_language = language_mismatch(question, text)
    if wrong_language:
        h.fail(wrong_language)

    lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 10]
    if lines:
        most, count = Counter(lines).most_common(1)[0]
        if count >= 5:
            h.warn("repeats the same line many times")
    return h


def quick_checks(question: str, answer: str) -> Heuristics:
    """The quick checks: empty, refusal, mostly in the wrong language. All are hard failures."""
    h = Heuristics()
    text = answer.strip()
    if not text:
        h.fail("empty answer")
        return h
    if _REFUSAL.match(text):
        h.fail("refused to answer")
    wrong_language = language_mismatch(question, text)
    if wrong_language:
        h.fail(wrong_language)
    return h


def combine(
    heuristics: Heuristics,
    mode: str,
    judge_score: float | None = None,
    judge_issues: list[str] | None = None,
    judge_model: str | None = None,
) -> CheckResult:
    threshold = PASS_THRESHOLD.get(mode, PASS_THRESHOLD["auto"])
    issues = list(heuristics.issues)
    if judge_score is None:
        score = heuristics.score * NO_JUDGE_CAP
    else:
        issues.extend(i for i in (judge_issues or []) if i not in issues)
        score = max(0.0, min(1.0, judge_score / 10))
        score = max(0.0, score - 0.05 * heuristics.soft)
        if heuristics.hard_fail:
            score = min(score, HARD_FAIL_CAP)
    return CheckResult(
        score=round(score, 3),
        passed=score >= threshold and not heuristics.hard_fail,
        issues=issues,
        hard_fail=heuristics.hard_fail,
        judge_score=judge_score,
        judge_model=judge_model,
        heuristic_score=round(heuristics.score * NO_JUDGE_CAP, 3),
    )


@dataclass
class JudgeGrade:
    score: float
    issues: list[str]


def parse_judge(text: str, count: int) -> list[JudgeGrade] | None:
    """Grades for ``count`` candidates from the judge's reply, or None if it can't be read."""
    try:
        data = extract_json(text)
    except ValueError:
        data = None
    items: list[Any] = []
    if isinstance(data, dict):
        items = data.get("grades") or data.get("scores") or ([data] if "score" in data else [])
    elif isinstance(data, list):
        items = data
    grades: dict[int, JudgeGrade] = {}
    for position, item in enumerate(items):
        if not isinstance(item, dict) or "score" not in item:
            continue
        try:
            value = float(item["score"])
        except (TypeError, ValueError):
            continue
        index = item.get("id", position + 1)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = position + 1
        raw_issues = item.get("issues") or []
        issues = [str(i)[:200] for i in raw_issues if str(i).strip()][:5]
        grades[index] = JudgeGrade(max(0.0, min(10.0, value)), issues)
    if not grades and count == 1:
        match = re.search(r"score\D{0,10}(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            grades[1] = JudgeGrade(max(0.0, min(10.0, float(match.group(1)))), [])
    if not grades:
        return None
    return [grades.get(i + 1, JudgeGrade(0.0, ["not graded"])) for i in range(count)]
