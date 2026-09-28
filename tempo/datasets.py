"""Openly licensed public question sets for `tempo collect`, with their licences.

Every dataset records its licence and where it was checked, and none holds private personal
data by its own documentation. On top of that, items that look like they contain an email
address, phone number, IP address or card number are skipped. Each question keeps its
dataset's name, so every exported row says which licence its text came under.
"""

from __future__ import annotations

import gzip
import json
import random
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

MAX_CHARS = 2500  # longer items cost too much free quota for one decision example
TRANSLATE_TO = [
    "Hindi",
    "Gujarati",
    "Spanish",
    "French",
    "German",
    "Japanese",
    "Chinese",
    "Arabic",
    "Portuguese",
    "Tamil",
]

# Things that look like personal data. Items that match are skipped, not cleaned.
PERSONAL = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"  # email
    r"|\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,6}"  # international phone number
    r"|(?<![\w.])\(?\d{3}\)?[\s-]\d{3}[\s-]\d{4}(?![\w.])"  # phone number like (555) 123-4567
    r"|(?<!\d)(?:\d{4}[ -]){3}\d{1,7}(?!\d)"  # card number in groups of four
    r"|\b(?:\d{1,3}\.){3}\d{1,3}\b",  # IP address
)


@dataclass(frozen=True)
class Item:
    item_id: str
    text: str


@dataclass(frozen=True)
class Dataset:
    name: str
    title: str
    url: str
    filename: str
    license: str
    license_url: str
    personal_data: str  # what the dataset's own documentation says
    parse: Callable[[bytes], Iterator[Item]]
    weight: int = 1  # share of the interleaved order
    # False: its rows go only to the held-out test split of every export, never to training
    # (owner's decision, step 4: Dolly's CC-BY-SA licence).
    training: bool = True

    def info(self) -> dict[str, Any]:
        return {
            "dataset": self.name,
            "title": self.title,
            "license": self.license,
            "license_url": self.license_url,
            "url": self.url,
            "training": self.training,
        }


def _jsonl(raw: bytes) -> Iterator[dict[str, Any]]:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    for line in raw.decode("utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _gsm8k(raw: bytes) -> Iterator[Item]:
    for i, row in enumerate(_jsonl(raw)):
        yield Item(str(i), row["question"].strip())


def _mbpp(raw: bytes) -> Iterator[Item]:
    for row in _jsonl(raw):
        tests = "\n".join(row.get("test_list") or [])
        text = f"{row['text'].strip()} Write it in Python."
        if tests:
            text += f"\nYour code should pass these tests:\n{tests}"
        yield Item(str(row["task_id"]), text)


def _humaneval(raw: bytes) -> Iterator[Item]:
    for row in _jsonl(raw):
        text = (
            "Complete this Python function. Reply with the whole function in a ```python "
            f"block.\n\n```python\n{row['prompt'].rstrip()}\n```"
        )
        yield Item(row["task_id"].replace("/", "_"), text)


def _dolly(raw: bytes) -> Iterator[Item]:
    for i, row in enumerate(_jsonl(raw)):
        text = row["instruction"].strip()
        context = (row.get("context") or "").strip()
        if context:
            text += f"\n\nText:\n{context}"
        yield Item(str(i), text)


def _dolly_translate(raw: bytes) -> Iterator[Item]:
    # Short open questions from Dolly, asked for as translations (same licence as Dolly).
    for i, row in enumerate(_jsonl(raw)):
        text = row["instruction"].strip()
        if row.get("context") or not 20 <= len(text) <= 200:
            continue
        language = TRANSLATE_TO[i % len(TRANSLATE_TO)]
        yield Item(str(i), f"Translate into {language}:\n{text}")


# Labels OpenAssistant's reviewers put on messages; any of these above the threshold means the
# message is skipped (flagged).
OASST_FLAGS = (
    "spam",
    "pii",
    "not_appropriate",
    "hate_speech",
    "sexual_content",
    "violence",
    "lang_mismatch",
)
OASST_FLAG_THRESHOLD = 0.3


def _oasst2(raw: bytes) -> Iterator[Item]:
    """OpenAssistant oasst2: questions only (the first user turn of each conversation), never
    deleted, reviewed as fine, not synthetic, and not flagged; scrubbed of personal data."""
    from tempo.privacy import scrub

    for row in _jsonl(raw):
        if row.get("role") != "prompter" or row.get("parent_id") is not None:
            continue
        if row.get("deleted") or row.get("review_result") is False or row.get("synthetic"):
            continue
        labels = row.get("labels") or {}
        flagged = any(
            (labels.get(name) or {}).get("value", 0) > OASST_FLAG_THRESHOLD for name in OASST_FLAGS
        )
        toxic = ((row.get("detoxify") or {}).get("toxicity") or 0) > 0.5
        if flagged or toxic:
            continue
        text = scrub((row.get("text") or "").strip())
        if text:
            yield Item(row["message_id"], text)


DOLLY_URL = (
    "https://huggingface.co/datasets/databricks/databricks-dolly-15k/resolve/main/"
    "databricks-dolly-15k.jsonl"
)
DOLLY_LICENSE = "https://huggingface.co/datasets/databricks/databricks-dolly-15k"
DOLLY_PRIVACY = (
    "Dataset card: \"To our knowledge, there are no private person's personal identifiers or "
    'sensitive information." It contains public information such as Wikipedia passages.'
)

DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in [
        Dataset(
            name="gsm8k",
            title="GSM8K grade-school maths word problems (OpenAI)",
            url="https://raw.githubusercontent.com/openai/grade-school-math/master/"
            "grade_school_math/data/train.jsonl",
            filename="gsm8k-train.jsonl",
            license="MIT",
            license_url="https://github.com/openai/grade-school-math/blob/master/LICENSE",
            personal_data="Invented word problems; the names in them are fictional.",
            parse=_gsm8k,
        ),
        Dataset(
            name="mbpp",
            title="MBPP: Mostly Basic Python Problems (Google Research)",
            url="https://raw.githubusercontent.com/google-research/google-research/master/"
            "mbpp/mbpp.jsonl",
            filename="mbpp.jsonl",
            license="CC-BY-4.0",
            license_url="https://huggingface.co/datasets/google-research-datasets/mbpp",
            personal_data="Programming exercises; no personal data.",
            parse=_mbpp,
        ),
        Dataset(
            name="humaneval",
            title="HumanEval Python functions (OpenAI)",
            url="https://raw.githubusercontent.com/openai/human-eval/master/data/"
            "HumanEval.jsonl.gz",
            filename="HumanEval.jsonl.gz",
            license="MIT",
            license_url="https://github.com/openai/human-eval/blob/master/LICENSE",
            personal_data="Programming exercises; no personal data.",
            parse=_humaneval,
        ),
        Dataset(
            name="oasst2",
            title="OpenAssistant oasst2: first user turns (questions only)",
            url="https://huggingface.co/datasets/OpenAssistant/oasst2/resolve/main/"
            "2023-11-05_oasst2_prompts.messages.jsonl.gz",
            filename="oasst2-prompts.messages.jsonl.gz",
            license="Apache-2.0",
            license_url="https://huggingface.co/datasets/OpenAssistant/oasst2",
            personal_data="Crowd-written prompts; messages flagged for personal data, spam or "
            "abuse, deleted or rejected in review are skipped, and the rest is scrubbed.",
            parse=_oasst2,
            weight=3,  # general questions in many languages: the permissive replacement for Dolly
        ),
        Dataset(
            name="dolly",
            title="databricks-dolly-15k instructions (Databricks)",
            url=DOLLY_URL,
            filename="databricks-dolly-15k.jsonl",
            license="CC-BY-SA-3.0",
            license_url=DOLLY_LICENSE,
            personal_data=DOLLY_PRIVACY,
            parse=_dolly,
            weight=3,  # covers chat, reasoning, writing, summarizing and extraction
            training=False,  # test only
        ),
        Dataset(
            name="dolly-translate",
            title="databricks-dolly-15k questions asked as translations",
            url=DOLLY_URL,
            filename="databricks-dolly-15k.jsonl",
            license="CC-BY-SA-3.0",
            license_url=DOLLY_LICENSE,
            personal_data=DOLLY_PRIVACY,
            parse=_dolly_translate,
            training=False,  # test only
        ),
    ]
}


def dataset_info(name: str | None) -> dict[str, Any] | None:
    dataset = DATASETS.get(name or "")
    return dataset.info() if dataset else None


def usable(text: str) -> str | None:
    """Why an item should be skipped, or None."""
    if len(text) > MAX_CHARS:
        return "too long"
    if PERSONAL.search(text):
        return "may contain personal data"
    return None


def interleave(items: dict[str, list[Item]], seed: int = 17) -> Iterator[tuple[str, Item]]:
    """A fixed, shuffled, round-robin order across datasets (weighted), so every run and every
    resume walks the same sequence and each task type shows up early."""
    queues = {}
    for name, rows in items.items():
        rows = list(rows)
        random.Random(f"{seed}:{name}").shuffle(rows)
        queues[name] = rows
    turns = [name for name in items for _ in range(DATASETS[name].weight)]
    position = dict.fromkeys(queues, 0)
    while any(position[n] < len(queues[n]) for n in queues):
        for name in turns:
            if position[name] < len(queues[name]):
                yield name, queues[name][position[name]]
                position[name] += 1
