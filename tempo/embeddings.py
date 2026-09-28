"""Embedding-based task classification and the semantic cache.

Both use a small local embedding model through fastembed (optional:
``pip install "tempo-server[embeddings]"``). Without it, the analyzer keeps its keyword rules
and the cache matches exact (normalized) questions only.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol

import yaml

from tempo.analyzer import analyze, classify, detect_script
from tempo.prompts import last_user_text
from tempo.types import TASKS, QueryProfile

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, ~65 MB, English
K_NEIGHBOURS = 7
MIN_SIMILARITY = 0.55  # below this the nearest example is not really alike
MIN_SHARE = 0.5  # the winning task's share of neighbour weight
RULES_CONFIDENT = 3.0  # keyword score at which the rules are trusted over embeddings


class Encoder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class FastEmbedEncoder:
    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from fastembed import TextEmbedding  # optional dependency

        self._model = TextEmbedding(model_name)
        self._lock = threading.Lock()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            return [_normalize(list(map(float, v))) for v in self._model.embed(list(texts))]


def load_encoder(setting: str, model_name: str | None = None) -> Encoder | None:
    if setting == "off":
        return None
    try:
        return FastEmbedEncoder(model_name or DEFAULT_MODEL)
    except ImportError:
        log.info("fastembed not installed; task classification uses keyword rules only")
    except Exception as exc:  # e.g. model download failed
        log.warning("Embedding model failed to load (%s); using keyword rules only", exc)
    return None


def load_examples() -> dict[str, list[str]]:
    text = resources.files("tempo").joinpath("data/task_examples.yaml").read_text("utf-8")
    data = yaml.safe_load(text) or {}
    return {task: [str(x) for x in data.get(task) or []] for task in TASKS}


@dataclass
class Vote:
    task: str
    share: float
    similarity: float


class EmbeddingClassifier:
    """k-nearest-neighbour vote over labelled example requests, combined with the rules.

    Embeddings win only where the rules are unsure (weak keyword evidence) and the neighbours
    clearly agree. Non-Latin requests keep the rules unless the model is multilingual.
    """

    def __init__(
        self,
        encoder_factory: Callable[[], Encoder | None],
        examples: dict[str, list[str]] | None = None,
        multilingual: bool = False,
    ) -> None:
        self._factory = encoder_factory
        self._examples = examples or load_examples()
        self._multilingual = multilingual
        self.encoder: Encoder | None = None
        self._vectors: list[tuple[str, list[float]]] = []
        self.status = "loading"

    def load(self) -> None:
        """Load the model and embed the examples (call from a worker thread)."""
        try:
            encoder = self._factory()
            if encoder is None:
                self.status = "unavailable"
                return
            pairs = [(task, text) for task, texts in self._examples.items() for text in texts]
            vectors = encoder.embed([text for _, text in pairs])
            self._vectors = [(task, vec) for (task, _), vec in zip(pairs, vectors, strict=True)]
            self.encoder = encoder
            self.status = "ready"
        except Exception as exc:
            log.warning("Embedding classifier failed to load: %s", exc)
            self.status = "error"

    def vote(self, vector: Sequence[float]) -> Vote | None:
        if not self._vectors:
            return None
        scored = sorted(((cosine(vector, v), t) for t, v in self._vectors), reverse=True)
        top = scored[:K_NEIGHBOURS]
        weights: dict[str, float] = {}
        for similarity, task in top:
            weights[task] = weights.get(task, 0.0) + max(similarity, 0.0)
        total = sum(weights.values()) or 1.0
        task, weight = max(weights.items(), key=lambda kv: kv[1])
        best_similarity = max(sim for sim, t in top if t == task)
        return Vote(task=task, share=weight / total, similarity=best_similarity)

    async def refine(
        self, messages: list[dict[str, Any]], profile: QueryProfile
    ) -> tuple[QueryProfile, str]:
        if self.status != "ready" or self.encoder is None:
            return profile, "rules"
        text = last_user_text(messages).strip()
        if not text or (detect_script(text) != "latin" and not self._multilingual):
            return profile, "rules"
        encoder = self.encoder
        try:
            vector = (await asyncio.to_thread(encoder.embed, [text[:2000]]))[0]
        except Exception as exc:
            log.warning("Embedding failed (%s); keeping the rules", exc)
            return profile, "rules"
        vote = self.vote(vector)
        if vote is None:
            return profile, "rules"
        rule_task, scores = classify(text)
        rules_strength = max(scores.values(), default=0.0)
        if vote.task == rule_task:
            return profile, "rules + embeddings"
        if (
            rules_strength < RULES_CONFIDENT
            and vote.share >= MIN_SHARE
            and vote.similarity >= MIN_SIMILARITY
        ):
            return analyze(messages, task=vote.task), "embeddings"  # type: ignore[arg-type]
        return profile, "rules"


# --- semantic cache ------------------------------------------------------------------------

_TIME_SENSITIVE = re.compile(
    r"\b(today|tonight|now|right now|current(ly)?|latest|recent|news|this (week|month|year)|"
    r"yesterday|tomorrow|price|stock|weather|score|live|trending)\b",
    re.IGNORECASE,
)


def normalize_question(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def time_sensitive(text: str) -> bool:
    return bool(_TIME_SENSITIVE.search(text))


@dataclass
class CacheEntry:
    key: str
    scope: str
    mode: str
    answer: str
    model: str
    created: float
    vector: list[float] | None = None


@dataclass
class CacheHit:
    answer: str
    model: str
    similarity: float
    age_s: float


class SemanticCache:
    """Recent checked answers to single-turn questions, scoped per user.

    A hit needs the same mode and either the same normalized question or, with an encoder,
    cosine similarity of at least ``threshold``. Time-sensitive questions are never cached.
    """

    def __init__(
        self,
        encoder: Callable[[], Encoder | None] | None = None,
        ttl_s: float = 24 * 3600,
        max_entries: int = 1000,
        threshold: float = 0.97,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._encoder = encoder or (lambda: None)
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self.threshold = threshold
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str, str], CacheEntry] = OrderedDict()

    def _live(self) -> list[CacheEntry]:
        now = self._clock()
        for key in [k for k, e in self._entries.items() if now - e.created > self.ttl_s]:
            del self._entries[key]
        return list(self._entries.values())

    async def lookup(self, question: str, mode: str, scope: str = "local") -> CacheHit | None:
        if time_sensitive(question):
            return None
        key = normalize_question(question)
        live = self._live()
        exact = self._entries.get((scope, mode, key))
        if exact is not None:
            self._entries.move_to_end((scope, mode, key))
            return CacheHit(exact.answer, exact.model, 1.0, self._clock() - exact.created)
        encoder = self._encoder()
        candidates = [e for e in live if e.scope == scope and e.mode == mode and e.vector]
        if encoder is None or not candidates:
            return None
        vector = (await asyncio.to_thread(encoder.embed, [question[:2000]]))[0]
        best = max(candidates, key=lambda e: cosine(vector, e.vector or []))
        similarity = cosine(vector, best.vector or [])
        if similarity < self.threshold:
            return None
        return CacheHit(best.answer, best.model, similarity, self._clock() - best.created)

    def store(
        self, question: str, mode: str, answer: str, model: str, scope: str = "local"
    ) -> None:
        if time_sensitive(question):
            return
        entry = CacheEntry(
            key=normalize_question(question),
            scope=scope,
            mode=mode,
            answer=answer,
            model=model,
            created=self._clock(),
        )
        self._entries[(scope, mode, entry.key)] = entry
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        encoder = self._encoder()
        if encoder is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                entry.vector = encoder.embed([question[:2000]])[0]
                return
            task = loop.run_in_executor(None, encoder.embed, [question[:2000]])
            task.add_done_callback(
                lambda done: (
                    setattr(entry, "vector", done.result()[0]) if not done.exception() else None
                )
            )
