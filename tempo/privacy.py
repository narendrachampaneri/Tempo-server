"""Personal data scrubbing and training consent.

Before any training export, text from Tempo's own traffic is scrubbed: emails, phone numbers,
card numbers, IP addresses, and Indian Aadhaar and PAN numbers become placeholders. Questions
from users other than the owner are exported only if that user opted in (off by default, and it
can be withdrawn); any user can delete their data.
"""

from __future__ import annotations

import re

# Order matters: the longer, more specific patterns first.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[card]", re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{1,7}(?!\d)")),
    ("[aadhaar]", re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")),
    ("[pan]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[ip]", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("[phone]", re.compile(r"\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,6}")),
    ("[phone]", re.compile(r"(?<![\w.])\(?\d{3}\)?[\s-]\d{3}[\s-]\d{4}(?![\w.])")),
    ("[phone]", re.compile(r"(?<![\w.+])[6-9]\d{9}(?![\w.])")),  # Indian mobile numbers
)


def scrub(text: str) -> str:
    """Replace personal data with placeholders such as [email] or [phone]."""
    for placeholder, pattern in _PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def scrub_value(value):
    """Scrub every string inside a JSON-like value (messages, states)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    return value
