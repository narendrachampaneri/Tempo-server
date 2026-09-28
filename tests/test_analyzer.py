import pytest

from tempo.analyzer import analyze, detect_script, message_text


def profile(text: str):
    return analyze([{"role": "user", "content": text}])


@pytest.mark.parametrize(
    ("text", "task"),
    [
        ("Write a Python function that reverses a linked list", "code"),
        ("Why does this crash?\n```js\nconsole.log(x.y)\n```", "code"),
        ("What is 17% of 2,340?", "math"),
        ("Solve the equation 3x + 5 = 20", "math"),
        (
            "A train travels 240 km in 3 hours, then 180 km in 2 hours. "
            "What is its average speed in km/h?",
            "math",
        ),
        ("How many apples are left if I have 12 and give away 5?", "math"),
        ("What happened in 1947 and why does it matter?", "reasoning"),
        ('Translate "Where is the station?" into Hindi', "translate"),
        ("Summarize this article in three bullet points: ...", "summarize"),
        ("Extract all email addresses from this text and return JSON", "extract"),
        ("Write a short poem about the monsoon", "writing"),
        ("Compare REST and GraphQL for a mobile app", "reasoning"),
        ("hi there!", "chat"),
    ],
)
def test_classifies_task(text, task):
    assert profile(text).task == task


def test_harder_prompts_score_higher_complexity():
    easy = profile("Write a function that adds two numbers")
    hard = profile(
        "Write a production-ready, thread-safe LRU cache in Python. It must handle concurrent "
        "access, should be efficient, and must include unit tests for edge cases.\n"
        "1. O(1) get\n2. O(1) put\n3. TTL expiry"
    )
    assert hard.task == easy.task == "code"
    assert hard.complexity > easy.complexity + 0.3
    assert "reasoning" in hard.needs


def test_detects_non_latin_scripts():
    assert detect_script("નમસ્તે, તમે કેમ છો?") == "gujarati"
    assert detect_script("आप कैसे हैं?") == "devanagari"
    assert detect_script("Hello, how are you?") == "latin"
    assert detect_script("12345 !!!") == "latin"


def test_needs_json_and_long_context():
    assert "json" in profile("List the planets as JSON").needs
    long_text = "Summarize this: " + "word " * 40_000
    p = profile(long_text)
    assert "long_context" in p.needs
    assert p.input_tokens > 8000


def test_image_parts_require_vision():
    p = analyze(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this picture?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]
    )
    assert p.has_images
    assert "vision" in p.needs


def test_classifies_latest_user_message_but_counts_whole_conversation():
    messages = [
        {"role": "user", "content": "Write a Python script " + "x " * 400},
        {"role": "assistant", "content": "Sure, here it is " + "y " * 400},
        {"role": "user", "content": "Thanks! Now write a haiku about it."},
    ]
    p = analyze(messages)
    assert p.task == "writing"
    assert p.input_tokens > 300


def test_message_text_handles_part_lists():
    parts = [{"type": "text", "text": "a"}, {"type": "image_url"}, {"type": "text", "text": "b"}]
    assert message_text(parts) == "a\nb"
    assert message_text(None) == ""
