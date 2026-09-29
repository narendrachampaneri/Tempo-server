"""What Tempo-server offers to other assistants: ask, a second opinion, verification, the model
list and the free quota. Plain functions returning JSON-ready dicts; the MCP server
(tempo/mcp_server.py) exposes them as tools.

Everything runs through the engine, so routing, free-quota limits, fallbacks, health checks,
privacy options and each caller's own keys apply exactly as for any other question.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tempo import budget, catalog
from tempo.analyzer import analyze
from tempo.checks import run_heuristics
from tempo.engine import DEFAULT_SYSTEM_PROMPT, RunResult
from tempo.types import Access

if TYPE_CHECKING:
    from tempo.engine import Engine

MODES = ("auto", "fast", "best")

COMPARE_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "comparison",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["agree", "partly", "disagree"]},
                "agree": {"type": "array", "items": {"type": "string"}},
                "differ": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "point": {"type": "string"},
                            "first": {"type": "string"},
                            "second": {"type": "string"},
                        },
                        "required": ["point", "first", "second"],
                        "additionalProperties": False,
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["verdict", "agree", "differ", "summary"],
            "additionalProperties": False,
        },
    },
}

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "score": {"type": "integer", "minimum": 0, "maximum": 10},
                "problems": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["verdict", "score", "problems", "summary"],
            "additionalProperties": False,
        },
    },
}

COMPARE_PROMPT = """Two AI models answered the same question independently. Compare them.
List the points where they agree, and every point where they differ (what the first says, what
the second says). Judge substance, not wording. Then say whether they agree, partly agree or
disagree, and summarise in one or two sentences which answer is more reliable and why.

Question:
{question}

First answer (model {first_model}):
{first}

Second answer (model {second_model}):
{second}"""

VERIFY_PROMPT = """You are a strict reviewer. Check the answer below for the question: is it
correct, complete, and does it answer what was asked? List every concrete problem (wrong facts,
wrong calculations, bugs, missing parts, unsupported claims). Give a score from 0 to 10 and a
verdict: "pass" only if the answer is correct and complete enough to rely on.

Question:
{question}

Answer to check:
{answer}"""


def _family(engine: Engine, model_id: str | None) -> str | None:
    if not model_id:
        return None
    model = engine.registry.get(model_id)
    return model.family if model is not None else None


def _summary(engine: Engine, result: RunResult) -> dict[str, Any]:
    """The answer, which models took part, and what the checks said."""
    used: list[str] = []
    checks: list[dict[str, Any]] = []
    notes: list[str] = []
    for event in result.events:
        data = event.data
        if event.type == "call_end" and data.get("model") not in used:
            used.append(data["model"])
        elif event.type == "check":
            for r in data.get("results") or []:
                checks.append(
                    {
                        "model": r.get("model"),
                        "passed": r.get("passed"),
                        "score": r.get("score"),
                        "issues": r.get("issues") or [],
                        "judge": r.get("judge_model"),
                    }
                )
        elif event.type == "note":
            notes.append(data.get("message", ""))
    out: dict[str, Any] = {
        "answer": result.text,
        "model": result.model,
        "family": _family(engine, result.model),
        "models_used": used,
        "checks": checks,
        "stages": result.stages,
        "stop_reason": result.stop_reason,
        "score": result.score,
    }
    if notes:
        out["notes"] = notes
    if result.error:
        out["error"] = result.error
    return out


async def ask(
    engine: Engine,
    question: str,
    *,
    mode: str = "auto",
    private: bool = False,
    local_only: bool = False,
    access: Access | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Answer a question the usual Tempo way: route, draft, check, fix when weak.

    ``private``: never send it to a model whose free tier may log or train on prompts.
    ``local_only``: only local models (Ollama); nothing leaves the computer."""
    options = engine.options(
        mode=mode if mode in MODES else "auto",
        no_logging=private,
        local_only=local_only,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        access=access or Access(),
        source=source,
    )
    result = await engine.complete([{"role": "user", "content": question}], options)
    return _summary(engine, result)


async def _one(
    engine: Engine, messages: list[dict[str, Any]], access: Access, **options: Any
) -> RunResult:
    run = engine.options(max_stages=1, strategy="single", access=access, **options)
    return await engine.complete(messages, run)


async def second_opinion(
    engine: Engine,
    question: str,
    *,
    private: bool = False,
    access: Access | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Ask two different model families the same question, then have a third (when there is
    one) list where they agree and where they differ."""
    access = access or Access()
    user = [{"role": "user", "content": question}]
    common = {"no_logging": private, "system_prompt": DEFAULT_SYSTEM_PROMPT}
    first = await _one(engine, user, access, source=source, **common)
    if first.error:
        return {"error": first.error}
    first_family = _family(engine, first.model)
    second = await _one(
        engine, user, access, source=source, exclude_families=[first_family], **common
    )
    if second.error:
        return {
            "error": "Only one model family is available right now, so there is no second "
            f"opinion ({second.error})",
            "first": {"model": first.model, "family": first_family, "answer": first.text},
        }
    second_family = _family(engine, second.model)
    compare_prompt = COMPARE_PROMPT.format(
        question=question,
        first_model=first.model,
        first=first.text,
        second_model=second.model,
        second=second.text,
    )
    judge_input = [{"role": "user", "content": compare_prompt}]
    families = [f for f in (first_family, second_family) if f]
    compared = await _one(
        engine,
        judge_input,
        access,
        source=source,
        no_logging=private,
        response_format=COMPARE_SCHEMA,
        exclude_families=families,
    )
    if compared.error:  # no third family: one of the two compares
        compared = await _one(
            engine,
            judge_input,
            access,
            source=source,
            no_logging=private,
            response_format=COMPARE_SCHEMA,
        )
    comparison: dict[str, Any] | None = None
    if not compared.error:
        comparison = json.loads(compared.text)
        comparison["compared_by"] = compared.model
    return {
        "first": {"model": first.model, "family": first_family, "answer": first.text},
        "second": {"model": second.model, "family": second_family, "answer": second.text},
        "comparison": comparison,
        **({"comparison_error": compared.error} if compared.error else {}),
    }


async def verify(
    engine: Engine,
    question: str,
    answer: str,
    *,
    answer_model: str | None = None,
    private: bool = False,
    access: Access | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Check someone's answer with Tempo's quick checks and a judge from a different model
    family than the one that wrote it (``answer_model``: a model id or a family name)."""
    access = access or Access()
    exclude = _family(engine, answer_model) or answer_model
    profile = analyze([{"role": "user", "content": question}])
    heuristics = run_heuristics(profile, question, answer)
    judge_input = [
        {"role": "user", "content": VERIFY_PROMPT.format(question=question, answer=answer)}
    ]
    judged = await _one(
        engine,
        judge_input,
        access,
        source=source,
        no_logging=private,
        response_format=VERDICT_SCHEMA,
        exclude_families=[exclude] if exclude else None,
    )
    out: dict[str, Any] = {
        "quick_checks": {"issues": heuristics.issues, "hard_fail": heuristics.hard_fail},
    }
    if judged.error:
        out["error"] = judged.error
        out["verdict"] = "fail" if heuristics.hard_fail else "unknown"
        return out
    grade = json.loads(judged.text)
    problems = list(dict.fromkeys([*heuristics.issues, *grade.get("problems", [])]))
    verdict = "fail" if heuristics.hard_fail else grade["verdict"]
    out.update(
        {
            "verdict": verdict,
            "score": grade["score"],
            "problems": problems,
            "summary": grade["summary"],
            "judge": judged.model,
            "judge_family": _family(engine, judged.model),
        }
    )
    return out


def models(
    engine: Engine, access: Access | None = None, include_all: bool = False
) -> list[dict[str, Any]]:
    """The live free-model catalog with health, and whether each model is ready for this
    caller (their keys, the server's keys if shared, local models)."""
    access = access or Access()
    rows = catalog.rows(engine.registry, engine.health, everything=include_all)
    keep = (
        "provider",
        "model",
        "type",
        "context",
        "tools",
        "limits",
        "data_policy",
        "health",
        "status",
        "preview",
    )
    out = []
    for row in rows:
        model = engine.registry.get(row["model"])
        if model is None or (not include_all and not model.chat_capable):
            continue
        item = {k: row[k] for k in keep}
        item["ready"] = (
            model.chat_capable
            and engine.registry.is_configured(model.provider, access)
            and model.installed is not False
            and engine.health.unavailable_reason(model) is None
        )
        out.append(item)
    return out


def quota(engine: Engine, access: Access | None = None) -> dict[str, Any]:
    view = budget.quota_view(engine, access or engine.access_for("local"))
    return {
        "providers": [
            {**q.as_dict(), "resets_in": budget.resets_text(q.resets_in_s)} for q in view
        ],
        "all_used_up": budget.all_used_up(view),
    }
