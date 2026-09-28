"""Personal data scrubbing and training consent.

Before any training export, text from Tempo's own traffic is scrubbed: emails, phone numbers,
card numbers, IP addresses, US Social Security numbers, IBAN bank account numbers (checksum
verified), and Indian Aadhaar and PAN numbers become placeholders. Questions
from users other than the owner are exported only if that user opted in (off by default, and it
can be withdrawn); any user can delete their data.
"""

from __future__ import annotations

import re

# IBAN: two letters, two check digits, then 11-30 letters or digits (spaces allowed in groups
# of four). Only strings whose mod-97 checksum is right are replaced.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")


def _valid_iban(candidate: str) -> bool:
    compact = candidate.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


# Order matters: the longer, more specific patterns first.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[ssn]", re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])")),
    ("[card]", re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{1,7}(?!\d)")),
    ("[aadhaar]", re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")),
    ("[pan]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("[ip]", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("[phone]", re.compile(r"\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,6}")),
    ("[phone]", re.compile(r"(?<![\w.])\(?\d{3}\)?[\s-]\d{3}[\s-]\d{4}(?![\w.])")),
    ("[phone]", re.compile(r"(?<![\w.+])[6-9]\d{9}(?![\w.])")),  # Indian mobile numbers
)


def scrub(text: str) -> str:
    """Replace personal data with placeholders such as [email], [phone] or [iban]."""
    text = _IBAN.sub(lambda m: "[iban]" if _valid_iban(m.group(0)) else m.group(0), text)
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
