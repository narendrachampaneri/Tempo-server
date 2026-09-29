"""Query Analyzer: turns a conversation into a QueryProfile in well under a millisecond.

Phase 1 uses weighted keyword rules and simple heuristics. Phase 2 adds an embedding
classifier behind the same ``analyze`` function.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from tempo.types import QueryProfile, Task

_I = re.IGNORECASE
_M = re.MULTILINE

_LANGS = (
    "english|hindi|gujarati|marathi|tamil|telugu|bengali|urdu|punjabi|kannada|malayalam|"
    "spanish|french|german|italian|portuguese|russian|chinese|japanese|korean|arabic"
)

# (pattern, weight). The task with the highest total wins; below 1.0 means plain chat.
_RULES: dict[Task, list[tuple[re.Pattern[str], float]]] = {
    "code": [
        (re.compile(r"```"), 3.0),
        (
            re.compile(
                r"\b(python|javascript|typescript|java|c\+\+|c#|golang|rust|kotlin|swift|php|"
                r"ruby|sql|bash|shell|html|css|react|django|flask|fastapi|node\.?js|dockerfile)\b",
                _I,
            ),
            2.0,
        ),
        (
            re.compile(
                r"\b(function|class|method|variable|compile[rd]?|stack ?trace|traceback|exception|"
                r"bug|debug|refactor|unit tests?|regex|endpoint|algorithm|code|snippet|repo)\b",
                _I,
            ),
            1.5,
        ),
        (re.compile(r"^\s*(def |class |import |from \S+ import |#include|const |let )", _M), 2.0),
    ],
    "math": [
        (
            re.compile(
                r"\b(solve|equation|integral|derivative|differentiate|integrate|probability|"
                r"matrix|theorem|prove|proof|calculate|compute|percentage|algebra|geometry|"
                r"statistics|arithmetic)\b",
                _I,
            ),
            2.0,
        ),
        (re.compile(r"\d+(\.\d+)?\s*[-+*/^×÷=]\s*\d+"), 2.0),
        (re.compile(r"\d+(\.\d+)?\s*%"), 2.0),
        (re.compile(r"[∫∑√π≤≥≠∞]"), 2.0),
        # Word problems: at least two numbers plus a quantity word ("average speed", "how many").
        (
            re.compile(
                r"\A(?=.*?\d\D+\d)(?=.*?\b(how (many|much|long|far)|average|mean|median|ratio|"
                r"speed|velocity|distance|area|volume|perimeter|interest|profit|remainder|divisible|"
                r"total cost|in total|per (hour|day|week|month|year|km|kg|unit))\b)",
                _I | re.DOTALL,
            ),
            2.0,
        ),
    ],
    "translate": [
        (re.compile(r"\btranslat(e|ion|ing)\b", _I), 3.0),
        (re.compile(rf"\b(in|into|to) ({_LANGS})\b", _I), 1.5),
    ],
    "summarize": [
        (
            re.compile(
                r"\b(summari[sz]e|summary|tl;?dr|key points|main points|condense|shorten)\b", _I
            ),
            3.0,
        ),
    ],
    "extract": [
        (re.compile(r"\b(extract|parse|pull out|list all|find all)\b", _I), 2.0),
        (re.compile(r"\b(json|csv|yaml|table|structured)\b", _I), 1.0),
    ],
    "writing": [
        (re.compile(r"\b(write|draft|compose|rewrite|rephrase|paraphrase|proofread)\b", _I), 1.5),
        (
            re.compile(
                r"\b(poem|story|essay|email|letter|blog|article|tweet|caption|slogan|speech|"
                r"cover letter|lyrics|screenplay)\b",
                _I,
            ),
            2.0,
        ),
    ],
    "reasoning": [
        (
            re.compile(
                r"\b(why|explain|compare|difference between|pros and cons|trade-?offs?|"
                r"analy[sz]e|evaluate|should i|step by step|reason|plan|strategy|design)\b",
                _I,
            ),
            1.5,
        ),
    ],
}

_BASE_COMPLEXITY: dict[Task, float] = {
    "chat": 0.15,
    "code": 0.45,
    "math": 0.45,
    "reasoning": 0.40,
    "writing": 0.30,
    "summarize": 0.25,
    "translate": 0.20,
    "extract": 0.30,
}

_BASE_OUTPUT_TOKENS: dict[Task, int] = {
    "chat": 150,
    "code": 700,
    "math": 400,
    "reasoning": 500,
    "writing": 600,
    "summarize": 300,
    "translate": 300,
    "extract": 300,
}

# How long the answer will be: a whole file or app is long; "in one line" is short. Checked in
# order; the first match wins (tokens).
_LENGTH_HINTS: list[tuple[re.Pattern[str], int]] = [
    (
        re.compile(
            r"\b(complete|full|entire|whole|working|single[- ]file|self[- ]contained)\b"
            r"[^.?!\n]{0,40}"
            r"\b(html|web ?page|website|landing page|app|application|game|program|script|"
            r"file|module|dashboard|project)\b",
            _I,
        ),
        3500,
    ),
    (
        re.compile(
            r"\b(html|web ?page|landing page|website|game|dashboard|todo app|calculator app)\b"
            r"[^.?!\n]{0,40}\b(with|including|that has)\b",
            _I,
        ),
        2500,
    ),
    (re.compile(r"\b(essay|article|report|chapter|story|blog post|documentation)\b", _I), 1500),
    (
        re.compile(
            r"\b(in one (line|sentence|word)|one[- ]liner|briefly|in short|tl;?dr|yes or no|"
            r"just the (answer|number|name))\b",
            _I,
        ),
        60,
    ),
]
_WORD_COUNT = re.compile(r"\b(\d{2,5})[- ]?(words?|शब्द|શબ્દ)\b", _I)
_LINE_COUNT = re.compile(r"\b(\d{2,5})[- ]?(lines?)( of code)?\b", _I)


def expected_output_tokens(text: str, task: Task, last_tokens: int) -> int:
    """How many tokens the answer needs: from what was asked for (a full HTML file, 800 words,
    one line), else from the task type."""
    words = _WORD_COUNT.search(text)
    if words:
        return max(60, int(int(words.group(1)) * 1.4))
    lines = _LINE_COUNT.search(text)
    if lines and task == "code":
        return max(100, int(lines.group(1)) * 12)
    for pattern, tokens in _LENGTH_HINTS:
        if pattern.search(text):
            return tokens
    if task == "translate":
        return max(200, int(last_tokens * 1.2))
    if task == "summarize":
        return min(600, max(150, last_tokens // 5))
    return _BASE_OUTPUT_TOKENS[task]


_CONSTRAINTS = re.compile(
    r"\b(must|should|without|at least|at most|exactly|ensure|handle|edge cases?|"
    r"optimi[sz]e|efficient|include|avoid)\b",
    _I,
)
_MULTI_STEP = re.compile(r"\b(step by step|then|first,|finally|multiple|each of)\b", _I)
_HARD = re.compile(
    r"\b(prove|rigorous|optimi[sz]\w*|concurren\w*|distributed|complex|advanced|"
    r"edge cases?|production|scalab\w*|security)\b",
    _I,
)
# Fields that have specialist models. A specialist answers only questions in its own field.
_DOMAINS: dict[str, re.Pattern[str]] = {
    "finance": re.compile(
        r"\b(financ\w*|stocks?|invest\w*|portfolio|dividends?|tax(es)?|"
        r"loans?|mortgage|interest rates?|inflation|accounting|balance sheet|cash flow|"
        r"revenue|ebitda|valuation|bank(ing)?|credit (score|card)|budget(ing)?|mutual funds?|"
        r"crypto\w*|forex|gst|income tax)\b|शेयर|निवेश|कर्ज|ब्याज|રોકાણ|વ્યાજ|લોન",
        _I,
    ),
    "health": re.compile(
        r"\b(health|medical|medicine|medication|drugs?|dos(e|age)|symptoms?|diagnos\w*|disease|"
        r"illness|infection|doctor|patient|treatment|therap\w*|vaccin\w*|blood pressure|"
        r"diabet\w*|cancer|fever|pregnan\w*|allerg\w*|nutrition|clinical|hospital)\b|"
        r"दवा|बीमारी|डॉक्टर|बुखार|લક્ષણ|દવા|બીમારી|ડૉક્ટર|તાવ",
        _I,
    ),
}


def detect_domain(text: str) -> str | None:
    hits = {name: len(pattern.findall(text)) for name, pattern in _DOMAINS.items()}
    name, count = max(hits.items(), key=lambda item: item[1])
    return name if count else None


_LIST_ITEM = re.compile(r"^\s*([-*•]|\d+[.)])\s+", _M)
_JSON = re.compile(r"\bjson\b", _I)

# Unicode script ranges, used to notice non-Latin input and prefer multilingual models.
_SCRIPTS: tuple[tuple[str, int, int], ...] = (
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("arabic", 0x0600, 0x06FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("japanese", 0x3040, 0x30FF),
    ("korean", 0xAC00, 0xD7AF),
    ("cjk", 0x4E00, 0x9FFF),
)


def message_text(content: Any) -> str:
    """Text of an OpenAI-style message content (a string or a list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text"
        )
    return ""


def _has_image(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, Mapping) and part.get("type") in ("image_url", "input_image")
        for part in content
    )


def detect_script(text: str) -> str:
    counts: dict[str, int] = {}
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        code = ord(ch)
        for name, lo, hi in _SCRIPTS:
            if lo <= code <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not letters or not counts:
        return "latin"
    name, count = max(counts.items(), key=lambda item: item[1])
    return name if count / letters >= 0.2 else "latin"


def estimate_tokens(text: str, script: str = "latin") -> int:
    # ~4 characters per token for Latin text; non-Latin scripts tokenize less efficiently.
    per_token = 4 if script == "latin" else 2
    return max(1, len(text) // per_token)


def classify(text: str) -> tuple[Task, dict[Task, float]]:
    scores: dict[Task, float] = {}
    for task, rules in _RULES.items():
        total = sum(weight for pattern, weight in rules if pattern.search(text))
        if total:
            scores[task] = total
    if not scores or max(scores.values()) < 1.0:
        return "chat", scores
    return max(scores.items(), key=lambda item: item[1])[0], scores


def _complexity(text: str, task: Task, tokens: int) -> float:
    c = _BASE_COMPLEXITY[task]
    c += min(0.2, tokens / 4000)
    c += 0.05 * min(4, len(_CONSTRAINTS.findall(text)))
    c += 0.1 if _MULTI_STEP.search(text) else 0.0
    c += 0.1 if _HARD.search(text) else 0.0
    c += 0.05 * min(4, len(_LIST_ITEM.findall(text)))
    return round(min(1.0, max(0.0, c)), 2)


def analyze(messages: Iterable[Mapping[str, Any]], task: Task | None = None) -> QueryProfile:
    """Profile a conversation. ``task`` overrides the rule-based task (e.g. from embeddings)."""
    messages = list(messages)
    user_messages = [m for m in messages if m.get("role") == "user"]
    last_user = message_text(user_messages[-1].get("content")) if user_messages else ""
    all_text = "\n".join(message_text(m.get("content")) for m in messages)
    has_images = any(_has_image(m.get("content")) for m in messages)

    script = detect_script(last_user)
    if task is None:
        task, _ = classify(last_user)
    input_tokens = estimate_tokens(all_text, script)
    last_tokens = estimate_tokens(last_user, script)
    complexity = _complexity(last_user, task, last_tokens)

    est_output = expected_output_tokens(last_user, task, last_tokens)

    needs: list[str] = []
    if complexity >= 0.6 or (task in ("math", "reasoning") and complexity >= 0.45):
        needs.append("reasoning")
    if input_tokens > 8000:
        needs.append("long_context")
    if _JSON.search(last_user):
        needs.append("json")
    if has_images:
        needs.append("vision")

    return QueryProfile(
        task=task,
        complexity=complexity,
        script=script,
        needs=needs,
        input_tokens=input_tokens,
        est_output_tokens=est_output,
        has_images=has_images,
        domain=detect_domain(last_user),
    )
