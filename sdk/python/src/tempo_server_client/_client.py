from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any, Union

import httpx

from ._types import Answer, Event, ask_body, error_from, parse_line, parse_sse

DEFAULT_URL = "http://127.0.0.1:8000"
Rating = Union[int, str]


def _rating(rating: Rating) -> int:
    value = {"up": 1, "down": -1}.get(rating, rating) if isinstance(rating, str) else rating
    if value not in (1, -1):
        raise ValueError("rating must be 1 / -1 or 'up' / 'down'")
    return int(value)


def _settings(base_url: str | None, api_key: str | None) -> tuple:
    url = (base_url or os.environ.get("TEMPO_URL") or DEFAULT_URL).rstrip("/")
    key = api_key if api_key is not None else os.environ.get("TEMPO_API_KEY")
    headers = {"Accept": "application/json", "User-Agent": "tempo-server-client-python/0.1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return url, headers


class TempoClient:
    """Tempo-server's HTTP API. ``base_url`` defaults to $TEMPO_URL or http://127.0.0.1:8000;
    ``api_key`` (your Tempo key, not a provider key) to $TEMPO_API_KEY; none is needed for a
    server running just for you."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 300.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url, headers = _settings(base_url, api_key)
        self._http = http_client or httpx.Client(timeout=timeout)
        self._http.headers.update(headers)

    def __enter__(self) -> TempoClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, self.base_url + path, **kwargs)
        if response.status_code >= 400:
            raise error_from(response.status_code, response.content, response.headers)
        return response.json()

    # --- questions ----------------------------------------------------------------------

    def stream(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict[str, Any]] | None = None,
        mode: str = "auto",
        privacy: str = "default",
        model: str | None = None,
        **options: Any,
    ) -> Iterator[Event]:
        """Ask and stream every thinking-window event as it happens. ``mode``: auto, fast,
        best or private; ``privacy``: default, local_only or no_logging; ``options``:
        max_stages, time_budget_s, quota_budget, max_parallel, strategy, allow_providers."""
        body = ask_body(prompt, messages, mode, privacy, model, options)
        headers = {"Accept": "text/event-stream"}
        with self._http.stream(
            "POST", self.base_url + "/api/ask", json=body, headers=headers
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise error_from(response.status_code, response.content, response.headers)
            yield from parse_sse(response.iter_lines())

    def ask(self, prompt: str | None = None, **kwargs: Any) -> Answer:
        """Ask and wait for the checked final answer (``answer.events`` has the trace)."""
        answer = Answer()
        for event in self.stream(prompt, **kwargs):
            answer.apply(event)
        return answer

    def feedback(self, question_id: str, rating: Rating, comment: str | None = None) -> None:
        """👍 (1 or "up") or 👎 (-1 or "down") for an answer, saved for tuning."""
        body: dict[str, Any] = {"question_id": question_id, "rating": _rating(rating)}
        if comment:
            body["comment"] = comment
        self._json("POST", "/api/feedback", json=body)

    # --- information --------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/health")

    def me(self) -> dict[str, Any]:
        return self._json("GET", "/api/me")

    def models(self) -> dict[str, Any]:
        """Providers and models, ready or not (and why), with health and skills."""
        return self._json("GET", "/api/models")

    def quota(self) -> dict[str, Any]:
        """Free requests left today per provider, and whether everything is used up."""
        return self._json("GET", "/api/quota")

    # --- your data ----------------------------------------------------------------------

    def consent(self) -> bool:
        """Whether your questions may be used as training data (off by default)."""
        return bool(self._json("GET", "/api/consent")["consent"])

    def set_consent(self, consent: bool) -> bool:
        """Opt in to training use, or withdraw it."""
        return bool(self._json("PUT", "/api/consent", json={"consent": consent})["consent"])

    def delete_my_data(self) -> int:
        """Delete every question you asked (answers, decisions, feedback); returns how many."""
        return int(self._json("DELETE", "/api/data")["deleted_questions"])


class AsyncTempoClient:
    """The same as TempoClient, for asyncio."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url, headers = _settings(base_url, api_key)
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._http.headers.update(headers)

    async def __aenter__(self) -> AsyncTempoClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._http.request(method, self.base_url + path, **kwargs)
        if response.status_code >= 400:
            raise error_from(response.status_code, response.content, response.headers)
        return response.json()

    async def stream(
        self,
        prompt: str | None = None,
        *,
        messages: list[dict[str, Any]] | None = None,
        mode: str = "auto",
        privacy: str = "default",
        model: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Event]:
        body = ask_body(prompt, messages, mode, privacy, model, options)
        headers = {"Accept": "text/event-stream"}
        async with self._http.stream(
            "POST", self.base_url + "/api/ask", json=body, headers=headers
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise error_from(response.status_code, response.content, response.headers)
            async for line in response.aiter_lines():
                event = parse_line(line)
                if event is not None:
                    yield event

    async def ask(self, prompt: str | None = None, **kwargs: Any) -> Answer:
        answer = Answer()
        async for event in self.stream(prompt, **kwargs):
            answer.apply(event)
        return answer

    async def feedback(self, question_id: str, rating: Rating, comment: str | None = None) -> None:
        body: dict[str, Any] = {"question_id": question_id, "rating": _rating(rating)}
        if comment:
            body["comment"] = comment
        await self._json("POST", "/api/feedback", json=body)

    async def health(self) -> dict[str, Any]:
        return await self._json("GET", "/health")

    async def me(self) -> dict[str, Any]:
        return await self._json("GET", "/api/me")

    async def models(self) -> dict[str, Any]:
        return await self._json("GET", "/api/models")

    async def quota(self) -> dict[str, Any]:
        return await self._json("GET", "/api/quota")

    async def consent(self) -> bool:
        return bool((await self._json("GET", "/api/consent"))["consent"])

    async def set_consent(self, consent: bool) -> bool:
        return bool((await self._json("PUT", "/api/consent", json={"consent": consent}))["consent"])

    async def delete_my_data(self) -> int:
        return int((await self._json("DELETE", "/api/data"))["deleted_questions"])
