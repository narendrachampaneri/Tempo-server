"""A thin Python client for Tempo-server's HTTP API (no server code inside).

    from tempo_server_client import TempoClient

    tempo = TempoClient("http://127.0.0.1:8000", api_key="your-tempo-key")
    for event in tempo.stream("Explain TCP vs UDP"):
        if event.text:
            print("▸", event.text)          # the thinking window, live
    answer = tempo.ask("What is 17% of 2,340?")
    print(answer.text, answer.model)

``AsyncTempoClient`` has the same methods, as coroutines and async iterators.
"""

from ._client import AsyncTempoClient, TempoClient
from ._types import Answer, Event, TempoError

__all__ = ["Answer", "AsyncTempoClient", "Event", "TempoClient", "TempoError"]
__version__ = "0.1.0"
