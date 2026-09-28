"""Record sessions for the static public demo (docs/demo/index.html replays them).

`tempo-server record-demo` runs a few questions through the engine and saves every event the
thinking window shows (stages, calls, fallbacks, checks, the streamed answer) as
``docs/demo/recording.js``. Recorded in demo mode now (offline demo models); the owner re-records
with real models later. Nothing secret is in an event: no keys, and question ids and per-model
quota counters are dropped.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tempo import __version__
from tempo.engine import DEFAULT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from tempo.engine import Engine

QUESTIONS = [
    "Write a Python function that checks if a string is a palindrome, with tests",
    "What is 17% of 2,340?",
    'Translate "Where is the railway station?" into Hindi and Gujarati',
    "Explain the difference between TCP and UDP",
]
DROP_FIELDS = {"question_id", "quota"}
GLOBAL_NAME = "TEMPO_RECORDING"


def _clean(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k not in DROP_FIELDS}


async def record(engine: Engine, questions: list[str]) -> dict[str, Any]:
    await engine.startup(oneshot=True)
    sessions = []
    for question in questions:
        options = engine.options(system_prompt=DEFAULT_SYSTEM_PROMPT)
        events = [
            _clean(event.to_dict())
            async for event in engine.run([{"role": "user", "content": question}], options)
        ]
        sessions.append({"question": question, "events": events})
    return {
        "recorded_at": time.strftime("%Y-%m-%d", time.gmtime()),
        "version": __version__,
        "demo_mode": engine.registry.demo_mode,
        "sessions": sessions,
    }


def write(recording: dict[str, Any], out: Path) -> None:
    """A .js file (loads from file:// and GitHub Pages alike) or plain .json."""
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(recording, ensure_ascii=False, indent=1)
    if out.suffix == ".js":
        body = f"window.{GLOBAL_NAME} = {body};\n"
    out.write_text(body, encoding="utf-8")
