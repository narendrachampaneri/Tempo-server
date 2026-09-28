import litellm
import pytest

from tempo.providers import ThinkTagSplitter, classify_exception


def split(chunks: list[str]) -> list[tuple[str, str]]:
    splitter = ThinkTagSplitter()
    out = []
    for chunk in chunks:
        out.extend(splitter.feed(chunk))
    out.extend(splitter.flush())
    # merge adjacent deltas of the same kind for easy comparison
    merged: list[tuple[str, str]] = []
    for kind, text in out:
        if merged and merged[-1][0] == kind:
            merged[-1] = (kind, merged[-1][1] + text)
        else:
            merged.append((kind, text))
    return merged


def test_think_tags_split_across_chunks():
    assert split(["<thi", "nk>Let me ", "think.</th", "ink>\n\n", "Answer", "."]) == [
        ("reasoning", "Let me think."),
        ("answer", "Answer."),
    ]


def test_text_without_tags_passes_through():
    assert split(["Hello ", "world <b>", " x < y"]) == [("answer", "Hello world <b> x < y")]


def test_incomplete_tag_at_end_is_flushed_as_text():
    assert split(["a <thi"]) == [("answer", "a <thi")]


def test_unclosed_think_block_stays_reasoning():
    assert split(["<think>still thinking"]) == [("reasoning", "still thinking")]


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (litellm.RateLimitError("slow down", llm_provider="groq", model="m"), "rate_limit"),
        (litellm.AuthenticationError("bad key", llm_provider="groq", model="m"), "auth"),
        (
            litellm.ContextWindowExceededError("too long", model="m", llm_provider="groq"),
            "context",
        ),
        (litellm.Timeout("timeout", model="m", llm_provider="groq"), "timeout"),
        (litellm.NotFoundError("gone", model="m", llm_provider="groq"), "not_found"),
        (litellm.APIConnectionError("refused", llm_provider="groq", model="m"), "unavailable"),
        (litellm.BadRequestError("bad", model="m", llm_provider="groq"), "bad_request"),
        (ValueError("boom"), "unknown"),
    ],
)
def test_classifies_litellm_errors(exc, kind):
    assert classify_exception(exc).kind == kind


def test_rate_limit_reads_retry_after_header():
    exc = litellm.RateLimitError(
        "slow down", llm_provider="groq", model="m", headers={"retry-after": "7"}
    )
    assert classify_exception(exc).retry_after == 7.0


def test_status_code_fallback():
    class Weird(Exception):
        status_code = 503

    assert classify_exception(Weird("x")).kind == "unavailable"
