"""Voice input and read-aloud for the web app, through a provider's free speech models.

Only Groq is wired in: its free plan lists whisper-large-v3(-turbo) for speech-to-text and
Orpheus for text-to-speech (see models.yaml), on the OpenAI-style ``/audio/*`` routes
(https://console.groq.com/docs/text-to-speech and /docs/speech-to-text, checked 2026-09-29).
The web app shows the microphone and speaker buttons only when the caller has a usable key.
Nothing is used without a key, and a rate limit is reported, never worked around.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from tempo.types import ModelInfo

if TYPE_CHECKING:
    from tempo.engine import Engine
    from tempo.types import Access

GROQ_BASE = "https://api.groq.com/openai/v1"
PROVIDER = "groq"
# Orpheus: "Keep input text under 200 characters maximum per request."
# (console.groq.com/docs/model/canopylabs/orpheus-v1-english, checked 2026-09-29)
TTS_MAX_CHARS = 190
DEFAULT_VOICE = "hannah"  # one of Orpheus's voices named in Groq's docs (troy, hannah, austin)
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # a cautious cap: spoken questions are seconds long


class SpeechError(Exception):
    def __init__(
        self, status: int, message: str, code: str, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.message, self.code, self.retry_after = status, message, code, retry_after


def find_model(engine: Engine, access: Access, kind: str) -> ModelInfo | None:
    """The best usable speech model of this type: right provider, the caller's key works."""
    for model in engine.registry.all():
        if model.type != kind or model.provider != PROVIDER:
            continue
        if not engine.registry.is_configured(model.provider, access):
            continue
        if model.installed is False or model.listed is False:
            continue
        if engine.health.unavailable_reason(model):
            continue
        if kind == "text-to-speech" and "arabic" in model.id:
            continue  # the English voice is the default
        return model
    return None


def capabilities(engine: Engine, access: Access) -> dict[str, Any]:
    stt = find_model(engine, access, "speech-to-text")
    tts = find_model(engine, access, "text-to-speech")
    return {
        "transcribe": stt.id if stt else None,
        "say": tts.id if tts else None,
        "say_max_chars": TTS_MAX_CHARS,
    }


def split_for_speech(text: str, limit: int = TTS_MAX_CHARS) -> list[str]:
    """Sentence-sized pieces no longer than the model accepts, with markdown noise removed."""
    text = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = " ".join(text.split())
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 40 else limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + 1 + len(piece) <= limit:
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return merged


def _credentials(engine: Engine, access: Access) -> tuple[str, str]:
    creds = engine.registry.credentials(PROVIDER, access)
    key = creds.get("api_key")
    if not key:
        raise SpeechError(
            400,
            "Voice needs a Groq key. Add one on the Keys page (it is free).",
            "no_key",
        )
    return key, creds.get("api_base", GROQ_BASE)


def _explain(response: httpx.Response) -> SpeechError:
    status = response.status_code
    try:
        detail = str(response.json().get("error", {}).get("message", ""))
    except ValueError:
        detail = ""
    if status in (401, 403):
        return SpeechError(
            502, "Groq did not accept your key. Check it on the Keys page.", "key_rejected"
        )
    if status == 429:
        wait = response.headers.get("retry-after", "")
        return SpeechError(
            429,
            "The free voice limit is used up for now. Try again later, or type instead.",
            "rate_limited",
            int(wait) if wait.isdigit() else None,
        )
    if "terms" in detail.lower():
        return SpeechError(
            400,
            "Groq needs you to accept this voice model's terms once at "
            "console.groq.com/playground, then try again.",
            "terms_not_accepted",
        )
    return SpeechError(
        502,
        f"The voice service could not help ({status}). Try again in a moment.",
        "provider_error",
    )


async def transcribe(
    engine: Engine,
    access: Access,
    audio: bytes,
    content_type: str,
    *,
    language: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    model = find_model(engine, access, "speech-to-text")
    if model is None:
        raise SpeechError(
            400, "Voice input is not available: no speech model is ready.", "unavailable"
        )
    if not audio:
        raise SpeechError(
            400, "No audio was received. Hold the microphone button and speak.", "empty"
        )
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechError(
            413, "That recording is too long. Keep it under a few minutes.", "too_large"
        )
    key, base = _credentials(engine, access)
    extension = "webm" if "webm" in content_type else "ogg" if "ogg" in content_type else "wav"
    data = {"model": model.id.removeprefix(PROVIDER + "/"), "response_format": "json"}
    if language:
        data["language"] = language
    try:
        async with httpx.AsyncClient(timeout=60, transport=transport) as client:
            response = await client.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files={"file": (f"speech.{extension}", audio, content_type or "audio/webm")},
            )
    except httpx.HTTPError as exc:
        raise SpeechError(
            502, "Could not reach the voice service. Check your connection.", "unreachable"
        ) from exc
    if response.status_code != 200:
        raise _explain(response)
    return str(response.json().get("text", "")).strip()


async def say(
    engine: Engine,
    access: Access,
    text: str,
    *,
    voice: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bytes:
    model = find_model(engine, access, "text-to-speech")
    if model is None:
        raise SpeechError(
            400, "Read-aloud is not available: no speech model is ready.", "unavailable"
        )
    if not text.strip() or len(text) > TTS_MAX_CHARS + 10:
        raise SpeechError(
            400, f"Send between 1 and {TTS_MAX_CHARS} characters at a time.", "bad_length"
        )
    key, base = _credentials(engine, access)
    try:
        async with httpx.AsyncClient(timeout=60, transport=transport) as client:
            response = await client.post(
                f"{base}/audio/speech",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model.id.removeprefix(PROVIDER + "/"),
                    "input": text,
                    "voice": voice or DEFAULT_VOICE,
                    "response_format": "wav",
                },
            )
    except httpx.HTTPError as exc:
        raise SpeechError(
            502, "Could not reach the voice service. Check your connection.", "unreachable"
        ) from exc
    if response.status_code != 200:
        raise _explain(response)
    return response.content
