"""`tempo terms --check`: fetch each provider's terms page and check that the sentences each
training verdict and data policy rests on are still there, word for word.

A verdict whose quote is gone is reported as "changed" (the terms may have moved on); a page
that can't be read is "unreachable". Nothing is changed automatically: a person re-reads the
terms and updates models.yaml, with the new date.
"""

from __future__ import annotations

import html
import io
import logging
import re
from dataclasses import dataclass, field

import httpx

from tempo.registry import Registry

_QUOTED = re.compile(r"[\"“]([^\"“”]{25,})[\"”]")
_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.DOTALL | re.IGNORECASE)


@dataclass
class TermsCheck:
    provider: str
    url: str
    status: str  # ok | changed | unreachable | unchecked
    found: int = 0
    missing: list[str] = field(default_factory=list)
    note: str = ""


def quotes(text: str | None) -> list[str]:
    """The quoted sentences in a terms quote (at least 25 characters, so labels don't count)."""
    return [q.strip() for q in _QUOTED.findall(text or "")]


def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def page_text(content: bytes, content_type: str) -> str | None:
    if "pdf" in content_type or content[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader  # optional: only needed for PDF terms (NVIDIA)
        except ImportError:
            return None
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(io.BytesIO(content))
        return " ".join(page.extract_text() or "" for page in reader.pages)
    return html.unescape(_TAGS.sub(" ", content.decode("utf-8", errors="replace")))


async def check(
    registry: Registry, transport: httpx.AsyncBaseTransport | None = None
) -> list[TermsCheck]:
    results = []
    async with httpx.AsyncClient(
        transport=transport,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (tempo terms check)"},
    ) as client:
        for provider in registry.providers.values():
            if provider.local or provider.id == "mock" or not provider.training_terms_url:
                continue
            url = provider.training_terms_url
            wanted = quotes(provider.training_terms_quote)
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                results.append(TermsCheck(provider.id, url, "unreachable", note=str(exc)[:120]))
                continue
            text = page_text(response.content, response.headers.get("content-type", ""))
            if text is None:
                note = "PDF: install pypdf to check it"
                results.append(TermsCheck(provider.id, url, "unchecked", note=note))
                continue
            page = normalise(text)
            missing = [q for q in wanted if normalise(q) not in page]
            status = "ok" if wanted and not missing else ("changed" if missing else "unchecked")
            results.append(
                TermsCheck(provider.id, url, status, len(wanted) - len(missing), missing)
            )
    return results
