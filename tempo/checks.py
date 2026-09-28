"""Answer checking: is this answer good enough to stop, or does another stage need to run?

Cheap heuristics run on every answer (empty, cut off, refusal, broken JSON, Python syntax,
wrong script, repetition). An LLM judge from a different model family can add a 0-10 grade.
Heuristic hard failures always win over a judge's grade. Code is syntax-checked, never run.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tempo.analyzer import detect_script
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
_FENCE = re.compile(r"```([\w+-]*)\n(.*?)```", re.DOTALL)


@dataclass
class CheckResult:
    score: float
    passed: bool
    issues: list[str] = field(default_factory=list)
    hard_fail: bool = False
    judge_score: float | None = None  # 0-10 as the judge gave it
    judge_model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "passed": self.passed,
            "issues": self.issues,
            "hard_fail": self.hard_fail,
            "judge_score": self.judge_score,
            "judge_model": self.judge_model,
        }


@dataclass
class Heuristics:
    issues: list[str] = field(default_factory=list)
    hard_fail: bool = False

    def fail(self, issue: str) -> None:
        self.issues.append(issue)
        self.hard_fail = True

    def warn(self, issue: str) -> None:
        self.issues.append(issue)

    @property
    def score(self) -> float:
        soft = len(self.issues) - (1 if self.hard_fail else 0)
        base = HARD_FAIL_CAP if self.hard_fail else 1.0
        return max(0.0, base - SOFT_PENALTY * soft)


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
) -> Heuristics:
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

    if profile.task == "code":
        blocks = [(lang.lower(), code) for lang, code in _FENCE.findall(answer)]
        if not blocks:
            h.warn("no code block")
        for lang, code in blocks:
            if lang in ("python", "py", "python3"):
                try:
                    ast.parse(code)
                except SyntaxError as exc:
                    h.fail(f"Python syntax error on line {exc.lineno}: {exc.msg}")
                    break

    if len(text) < 40 and profile.est_output_tokens >= 300 and profile.task != "chat":
        h.warn("very short for this question")

    wanted = detect_script(question)
    if wanted != "latin" and profile.task != "translate" and detect_script(text) != wanted:
        h.warn(f"question is in {wanted} script but the answer is not")

    lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 10]
    if lines:
        most, count = Counter(lines).most_common(1)[0]
        if count >= 5:
            h.warn("repeats the same line many times")
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
        soft = len(heuristics.issues) - (1 if heuristics.hard_fail else 0)
        score = max(0.0, score - 0.05 * soft)
        if heuristics.hard_fail:
            score = min(score, HARD_FAIL_CAP)
    return CheckResult(
        score=round(score, 3),
        passed=score >= threshold and not heuristics.hard_fail,
        issues=issues,
        hard_fail=heuristics.hard_fail,
        judge_score=judge_score,
        judge_model=judge_model,
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
