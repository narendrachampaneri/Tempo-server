"""Embedding classifier and semantic cache, with a deterministic offline encoder."""

import asyncio
import hashlib
import math

from conftest import make_engine, user

from tempo.analyzer import analyze
from tempo.embeddings import (
    EmbeddingClassifier,
    SemanticCache,
    normalize_question,
    time_sensitive,
)

EXAMPLES = {
    "chat": ["hi there friend", "good morning how are you"],
    "code": ["write a function that sorts numbers", "fix my script bug crash"],
    "math": ["solve this equation", "what is seven times eight"],
    "reasoning": ["why does the ocean look blue", "compare two databases"],
    "writing": [
        "compose a heartfelt birthday note for grandma",
        "draft a heartfelt thank you note",
    ],
    "summarize": ["shorten this report", "key takeaways of the meeting"],
    "translate": ["say thank you in japanese", "put this into gujarati"],
    "extract": ["pull the emails from this text", "get all phone numbers"],
}


class BagOfWords:
    """Hashes words into a small vector: shared words mean similar vectors."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for text in texts:
            vec = [0.0] * 64
            for word in normalize_question(text).split():
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def loaded_classifier(encoder=None):
    classifier = EmbeddingClassifier(lambda: encoder or BagOfWords(), examples=EXAMPLES)
    classifier.load()
    return classifier


async def test_embeddings_decide_when_the_rules_have_no_keywords():
    classifier = loaded_classifier()
    messages = user("compose a heartfelt note for my grandma")
    rules_profile = analyze(messages)
    assert rules_profile.task == "writing" or rules_profile.task == "chat"
    profile, source = await classifier.refine(messages, analyze(messages))
    assert profile.task == "writing"
    assert source in ("embeddings", "rules + embeddings")


async def test_confident_rules_are_not_overridden():
    classifier = loaded_classifier()
    messages = user("```python\nprint(1)\n``` why does this python code print one")
    profile, source = await classifier.refine(messages, analyze(messages))
    assert profile.task == "code" and source in ("rules", "rules + embeddings")


async def test_non_latin_requests_keep_the_rules_with_an_english_model():
    classifier = loaded_classifier()
    messages = user("મારા માટે એક કવિતા લખો")
    profile, source = await classifier.refine(messages, analyze(messages))
    assert source == "rules"


async def test_unavailable_encoder_keeps_the_rules():
    classifier = EmbeddingClassifier(lambda: None, examples=EXAMPLES)
    classifier.load()
    assert classifier.status == "unavailable"
    messages = user("compose a heartfelt note")
    profile, source = await classifier.refine(messages, analyze(messages))
    assert source == "rules" and profile == analyze(messages)


async def test_engine_reports_the_classifier_as_the_source():
    engine, _ = make_engine(judge_score=9)
    engine.classifier = loaded_classifier()
    result = await engine.complete(user("compose a heartfelt birthday note for grandma"))
    analyze_event = next(e for e in result.events if e.type == "analyze")
    assert analyze_event.data["task"] == "writing"
    assert "embeddings" in analyze_event.data["source"]


def test_time_sensitive_questions():
    assert time_sensitive("What is the weather today?")
    assert time_sensitive("latest news on AI")
    assert not time_sensitive("Why is the sky blue?")


async def test_cache_exact_and_semantic_hits():
    now = [1000.0]
    encoder = BagOfWords()
    cache = SemanticCache(lambda: encoder, ttl_s=60, threshold=0.9, clock=lambda: now[0])
    cache.store("Why is the sky blue?", "auto", "Rayleigh scattering.", "m1")
    await asyncio.sleep(0.05)  # the embedding is computed in the background
    exact = await cache.lookup("why is the SKY blue", "auto")
    assert exact.answer == "Rayleigh scattering." and exact.similarity == 1.0
    similar = await cache.lookup("why is the sky so blue?", "auto")
    assert similar is not None and similar.similarity >= 0.9
    assert await cache.lookup("why is the sky blue", "best") is None  # other mode
    assert await cache.lookup("why is the sky blue", "auto", scope="bob") is None  # other user
    assert await cache.lookup("how do plants make food", "auto") is None
    now[0] += 61
    assert await cache.lookup("why is the sky blue", "auto") is None  # expired


async def test_cache_skips_time_sensitive_and_evicts_old_entries():
    cache = SemanticCache(max_entries=2)
    cache.store("price of gold today", "auto", "x", "m")
    assert await cache.lookup("price of gold today", "auto") is None
    for i in range(3):
        cache.store(f"question number {i}", "auto", str(i), "m")
    assert await cache.lookup("question number 0", "auto") is None
    assert (await cache.lookup("question number 2", "auto")).answer == "2"


async def test_engine_answers_repeat_questions_from_cache():
    engine, backend = make_engine(judge_score=9)
    engine.cache = SemanticCache()
    first = await engine.complete(user("Write a Python function that reverses a string"))
    calls = len(backend.calls)
    second = await engine.complete(user("write a python function that reverses a string!"))
    assert len(backend.calls) == calls  # no model call
    assert second.text == first.text and second.stop_reason == "cache"
    hit = next(e for e in second.events if e.type == "cache_hit")
    assert hit.to_dict()["text"].startswith("Answered from cache")
    # Conversations and explicit model requests are not served from cache.
    third = await engine.complete(
        user("write a python function that reverses a string"),
        engine.options(model="alpha/small"),
    )
    assert third.stop_reason != "cache"


async def test_failed_answers_are_not_cached():
    engine, _ = make_engine(judge_score=2)
    engine.cache = SemanticCache()
    await engine.complete(
        user("Write a Python function that reverses a string"), engine.options(max_stages=2)
    )
    again = await engine.complete(
        user("Write a Python function that reverses a string"), engine.options(max_stages=2)
    )
    assert again.stop_reason != "cache"
