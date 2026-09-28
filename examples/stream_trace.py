"""Stream every engine event (the thinking window) from POST /api/ask."""

import json

import httpx

question = {"prompt": "What is 17% of 2,340?", "mode": "auto"}
with httpx.stream("POST", "http://127.0.0.1:8000/api/ask", json=question, timeout=120) as reply:
    for line in reply.iter_lines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("text"):
            print("▸", event["text"])
        if event["type"] == "answer_final":
            print("\n" + event["answer"])
