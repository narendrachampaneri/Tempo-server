"""Which language a question expects its answer in, and whether an answer is mostly in it.

Owner's rule (step 2): mixed-language answers pass. An answer fails only when most of it is in a
different language or script from the question. Code, names, numbers, links and technical terms
don't count. If the user asks for a specific language, that language is expected instead.

Non-Latin scripts identify the language well enough (Devanagari covers Hindi and Marathi, which
is fine for this check). Latin-script languages are told apart by common function words, and
only when the guess is clear; otherwise the check stays silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tempo.analyzer import detect_script

# Language name (as users write it, in English or in the language) -> (script, latin code).
_LANGUAGES: dict[str, tuple[str, str | None]] = {
    "english": ("latin", "en"),
    "spanish": ("latin", "es"),
    "french": ("latin", "fr"),
    "german": ("latin", "de"),
    "italian": ("latin", "it"),
    "portuguese": ("latin", "pt"),
    "dutch": ("latin", "nl"),
    "hindi": ("devanagari", None),
    "marathi": ("devanagari", None),
    "nepali": ("devanagari", None),
    "gujarati": ("gujarati", None),
    "bengali": ("bengali", None),
    "bangla": ("bengali", None),
    "punjabi": ("gurmukhi", None),
    "tamil": ("tamil", None),
    "telugu": ("telugu", None),
    "kannada": ("kannada", None),
    "malayalam": ("malayalam", None),
    "urdu": ("arabic", None),
    "arabic": ("arabic", None),
    "russian": ("cyrillic", None),
    "ukrainian": ("cyrillic", None),
    "chinese": ("cjk", None),
    "mandarin": ("cjk", None),
    "japanese": ("japanese", None),
    "korean": ("korean", None),
    # Written in the language itself.
    "हिंदी": ("devanagari", None),
    "हिन्दी": ("devanagari", None),
    "अंग्रेज़ी": ("latin", "en"),
    "अंग्रेजी": ("latin", "en"),
    "ગુજરાતી": ("gujarati", None),
    "ગુજરાતીમાં": ("gujarati", None),
    "અંગ્રેજી": ("latin", "en"),
    "અંગ્રેજીમાં": ("latin", "en"),
    "hinglish": ("latin", None),
}
_NAMES = "|".join(sorted((re.escape(k) for k in _LANGUAGES), key=len, reverse=True))
# "answer in Hindi", "translate this into Gujarati", "... in Spanish please." at the end,
# "हिंदी में जवाब दीजिए", "ગુજરાતીમાં જવાબ આપો". A language merely mentioned ("why is namaste used
# in Hindi greetings?") is not a request.
_VERBS = (
    r"answer|reply|respond|write|explain|say|tell|translat\w*|summari[sz]e|describe|speak|"
    r"output|give|rewrite|convert"
)
_REQUESTS = (
    re.compile(rf"\b(?:{_VERBS})\b[^.?!\n]{{0,60}}?\b(?:in|into|to)\s+({_NAMES})\b", re.I),
    re.compile(rf"\b(?:in|into)\s+({_NAMES})\s*(?:please|only)?\s*[.?!]*\s*$", re.I),
    re.compile(rf"({_NAMES})\s*(?:में|માં)?\s*(?:जवाब|उत्तर|बताइए|बताओ|लिखिए|लिखो|જવાબ|લખો|જણાવો|કહો)"),
)

# Common function words per Latin-script language (short, distinctive, frequent).
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        "the and is are was of to in that it for with as on this what how why which be "
        "you can do does not".split()
    ),
    "es": frozenset(
        "el la los las es son de que y en un una por para con cómo qué cuál está no se".split()
    ),
    "fr": frozenset(
        "le la les est sont de des que et en un une pour avec dans ce qui ne pas".split()
    ),
    "de": frozenset(
        "der die das ist sind und zu den von mit ein eine nicht ich wie was für auf".split()
    ),
    "it": frozenset("il lo la gli le è sono di che e un una per con non come cosa della".split()),
    "pt": frozenset("o a os as é são de que e em um uma para com não como qual está do da".split()),
    "nl": frozenset("de het een is zijn en van dat niet met voor op hoe wat ik".split()),
}
_LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
}

_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\w+|\[[^\]]*\]\([^)]*\)")
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
MIN_WORDS = 6  # shorter answers are not judged


@dataclass(frozen=True)
class Expected:
    script: str
    latin: str | None = None  # "en", "es", ... for Latin-script languages, when known
    requested: bool = False

    @property
    def name(self) -> str:
        if self.latin:
            return _LANG_NAMES.get(self.latin, self.latin)
        return script_name(self.script)


_SCRIPT_NAMES = {
    "latin": "Latin script",
    "devanagari": "Hindi/Devanagari",
    "gujarati": "Gujarati",
    "gurmukhi": "Punjabi/Gurmukhi",
    "arabic": "Arabic script",
    "cyrillic": "Cyrillic script",
    "cjk": "Chinese",
    "japanese": "Japanese",
    "korean": "Korean",
}


def script_name(script: str) -> str:
    return _SCRIPT_NAMES.get(script, script.capitalize())


def requested_language(question: str) -> Expected | None:
    """The language the user explicitly asked for, if any ("answer in Hindi")."""
    for pattern in _REQUESTS:
        match = pattern.search(question.strip())
        if match:
            name = match.group(1).lower()
            script, latin = _LANGUAGES.get(name, ("", None))
            if script:
                return Expected(script, latin, requested=True)
    return None


def guess_latin(words: list[str]) -> str | None:
    """A Latin-script language from function words, only when the guess is clear."""
    lowered = [w.lower() for w in words]
    hits = {code: sum(w in stop for w in lowered) for code, stop in _STOPWORDS.items()}
    ranked = sorted(hits.items(), key=lambda item: item[1], reverse=True)
    (best, count), (_, second) = ranked[0], ranked[1]
    if count >= 3 and count >= 2 * second and count >= 0.15 * len(lowered):
        return best
    return None


def _clean(text: str) -> str:
    text = _CODE_BLOCK.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    return _LINK.sub(" ", text)


def _counted_words(text: str) -> list[str]:
    """Words that say which language the text is in: no code, links, numbers, or names and
    technical terms in Latin letters (capitalised words, ALLCAPS, camelCase, snake_case)."""
    out = []
    for word in _WORD.findall(_clean(text)):
        if detect_script(word) == "latin" and (
            word[0].isupper() or any(c.isupper() for c in word[1:])
        ):
            continue
        out.append(word)
    return out


def expected_language(question: str) -> Expected | None:
    requested = requested_language(question)
    if requested is not None:
        return requested
    words = _WORD.findall(_clean(question))
    counted = [detect_script(w) for w in _counted_words(question)]
    others = [c for c in counted if c != "latin"]
    if others:
        main = max(set(others), key=others.count)
        if others.count(main) * 3 >= len(counted):  # a third is enough: terms are often English
            return Expected(main)
    latin = guess_latin(words)
    return Expected("latin", latin) if latin else None


def mismatch(question: str, answer: str) -> str | None:
    """Why the answer is mostly in the wrong language, or None if it is fine or unclear."""
    expected = expected_language(question)
    if expected is None:
        return None
    words = _counted_words(answer)
    if len(words) < MIN_WORDS:
        return None
    scripts = [detect_script(w) for w in words]
    in_script = sum(s == expected.script for s in scripts)
    asked = "the requested language" if expected.requested else "the question's language"
    if in_script * 2 < len(words):
        others = [s for s in scripts if s != expected.script]
        main = max(set(others), key=others.count)
        found = script_name(main)
        if main == "latin":
            guess = guess_latin([w for w, s in zip(words, scripts, strict=True) if s == "latin"])
            found = _LANG_NAMES.get(guess, found) if guess else found
        return f"answer is mostly in {found}, not {expected.name} ({asked})"
    if expected.script == "latin" and expected.latin:
        latin_words = [w for w, s in zip(words, scripts, strict=True) if s == "latin"]
        guess = guess_latin(latin_words)
        if guess and guess != expected.latin:
            return f"answer is mostly in {_LANG_NAMES[guess]}, not {expected.name} ({asked})"
    return None
