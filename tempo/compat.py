"""OpenAI API features that must work the same on every provider: tool calls and strict JSON.

**Tool calls.** Models that support tools natively get the request's ``tools`` as is (LiteLLM
turns their replies into OpenAI tool calls). Every other model gets the tools described in a
system message and answers with ``<tool_call>{...}</tool_call>``. Either way, the reply is also
searched for the formats open models print as text (Hermes/Qwen ``<tool_call>``, Mistral
``[TOOL_CALLS]``, Llama ``<|python_tag|>`` and ``<function=name>``, a bare or fenced JSON
object), and every call is validated: the function must exist, the arguments must be a JSON
object that matches its parameter schema, and a required call must be there. A reply that fails
is treated like a failed call, so the next model is tried.

**Strict JSON schema.** ``response_format`` of type ``json_schema`` (or ``json_object``): the
schema goes into a system message, the answer's JSON is extracted and validated against it,
and an answer that doesn't match is rejected, so another model is tried.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from tempo.analyzer import message_text

EMULATION_MARK = "[tempo-tools]"  # starts the system message that describes the tools
JSON_MARK = "[tempo-json]"  # starts the system message that describes the JSON schema


# --- tool calls ---------------------------------------------------------------------------------


@dataclass
class ToolRequest:
    tools: list[dict[str, Any]]
    tool_choice: Any = "auto"  # "auto" | "none" | "required" | {"type":"function","function":...}
    parallel: bool = True

    @property
    def by_name(self) -> dict[str, dict[str, Any]]:
        return {
            t["function"]["name"]: t["function"]
            for t in self.tools
            if t.get("type", "function") == "function" and t.get("function", {}).get("name")
        }

    @property
    def required_name(self) -> str | None:
        choice = self.tool_choice
        if isinstance(choice, Mapping):
            return (choice.get("function") or {}).get("name")
        return None

    @property
    def must_call(self) -> bool:
        return self.tool_choice == "required" or self.required_name is not None


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:24]


def make_call(name: str, arguments: Any, call_id: str | None = None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return {
        "id": call_id or new_call_id(),
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _call_from_obj(obj: Any) -> dict[str, Any] | None:
    """One tool call from the shapes models print: {"name", "arguments"|"parameters"}, or an
    OpenAI-style {"function": {"name", "arguments"}}."""
    if not isinstance(obj, Mapping):
        return None
    if isinstance(obj.get("function"), Mapping):
        fn = obj["function"]
        if fn.get("name"):
            return make_call(fn["name"], fn.get("arguments", {}), obj.get("id"))
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    if not isinstance(name, str) or not name:
        return None
    for key in ("arguments", "parameters", "args", "input"):
        if key in obj:
            return make_call(name, obj[key])
    return make_call(name, {})


def _calls_from_json(text: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(text)
    except ValueError:
        return []
    if isinstance(value, Mapping) and isinstance(value.get("tool_calls"), list):
        value = value["tool_calls"]
    items = value if isinstance(value, list) else [value]
    calls = [_call_from_obj(item) for item in items]
    return [c for c in calls if c is not None]


_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)
_MISTRAL = re.compile(r"\[TOOL_CALLS\]\s*(\[.*\]|\{.*\})", re.DOTALL)
_PYTHON_TAG = re.compile(r"<\|python_tag\|>\s*(\{.*\})", re.DOTALL)
_FUNCTION_TAG = re.compile(r"<function=([\w.-]+)>\s*(\{.*?\})\s*</function>", re.DOTALL)
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def parse_text_tool_calls(text: str, names: set[str]) -> tuple[list[dict[str, Any]], str]:
    """Tool calls printed as text, in any common format, and the text that is left.
    A bare JSON object counts only if it names a known function."""
    if not text:
        return [], text
    calls: list[dict[str, Any]] = []
    rest = text
    for match in _TAGGED.finditer(text):
        calls += _calls_from_json(match.group(1))
    if calls:
        return calls, _TAGGED.sub("", text).strip()
    for pattern in (_MISTRAL, _PYTHON_TAG):
        match = pattern.search(text)
        if match:
            calls = _calls_from_json(match.group(1))
            if calls:
                return calls, (text[: match.start()] + text[match.end() :]).strip()
    for match in _FUNCTION_TAG.finditer(text):
        try:
            calls.append(make_call(match.group(1), json.loads(match.group(2))))
        except ValueError:
            continue
    if calls:
        return calls, _FUNCTION_TAG.sub("", text).strip()
    for candidate in [m.group(1) for m in _FENCED.finditer(text)] + [text.strip()]:
        found = [c for c in _calls_from_json(candidate) if c["function"]["name"] in names]
        if found:
            rest = text.replace(candidate, "").replace("```json", "").replace("```", "").strip()
            return found, rest
    return [], text


def validate_tool_calls(calls: list[dict[str, Any]], request: ToolRequest) -> list[str]:
    """What is wrong with these calls, or [] if they can be sent to the client."""
    issues: list[str] = []
    known = request.by_name
    if request.tool_choice == "none" and calls:
        issues.append("tool_choice is none but the model called a tool")
    if request.must_call and not calls:
        issues.append("a tool call was required but the model answered in text")
    required = request.required_name
    for call in calls:
        name = call["function"]["name"]
        if name not in known:
            issues.append(f"unknown function {name!r}")
            continue
        if required and name != required:
            issues.append(f"function {required!r} was required, not {name!r}")
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except ValueError:
            issues.append(f"{name}: arguments are not valid JSON")
            continue
        if not isinstance(args, dict):
            issues.append(f"{name}: arguments must be a JSON object")
            continue
        schema = known[name].get("parameters") or {"type": "object"}
        issues += [f"{name}: {e}" for e in schema_errors(args, schema)]
    if not request.parallel and len(calls) > 1:
        issues.append("parallel_tool_calls is false but the model made several calls")
    return issues


def tool_system_prompt(request: ToolRequest) -> str:
    """How a model without native tool support is told about the tools."""
    listing = json.dumps(
        [
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
            for fn in request.by_name.values()
        ],
        ensure_ascii=False,
    )
    if request.required_name:
        rule = f"You must call the function {request.required_name!r}."
    elif request.tool_choice == "required":
        rule = "You must call at least one of the functions."
    else:
        rule = "Call a function only when it helps; otherwise answer normally in text."
    return (
        f"{EMULATION_MARK} You can call these functions:\n{listing}\n\n{rule} To call one, reply "
        'with exactly <tool_call>{"name": "<function name>", "arguments": {<arguments as '
        "JSON>}}</tool_call> (one block per call) and nothing else. The arguments must match "
        "the function's parameters schema."
    )


def emulated_messages(
    messages: Sequence[Mapping[str, Any]], request: ToolRequest
) -> list[dict[str, Any]]:
    """The conversation for a model without native tools: the tools as a system message, earlier
    tool calls as text, and tool results as user messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": tool_system_prompt(request)}]
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            blocks = [
                "<tool_call>"
                + json.dumps(
                    {
                        "name": c["function"]["name"],
                        "arguments": _loads_or_raw(c["function"].get("arguments")),
                    },
                    ensure_ascii=False,
                )
                + "</tool_call>"
                for c in m["tool_calls"]
            ]
            text = message_text(m.get("content"))
            out.append({"role": "assistant", "content": "\n".join([text, *blocks]).strip()})
        elif role == "tool":
            name = m.get("name") or m.get("tool_call_id") or "tool"
            result = message_text(m.get("content"))
            out.append({"role": "user", "content": f"Result of {name}:\n{result}"})
        else:
            out.append({k: v for k, v in m.items() if k in ("role", "content", "name")})
    return out


def _loads_or_raw(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# --- strict JSON --------------------------------------------------------------------------------


@dataclass
class JsonFormat:
    schema: dict[str, Any] | None = None  # None: any JSON object ("json_object")
    name: str = "response"
    strict: bool = True

    @classmethod
    def from_request(cls, response_format: Mapping[str, Any] | None) -> JsonFormat | None:
        if not response_format:
            return None
        kind = response_format.get("type")
        if kind == "json_object":
            return cls(schema=None)
        if kind == "json_schema":
            spec = response_format.get("json_schema") or {}
            return cls(
                schema=spec.get("schema") or {},
                name=spec.get("name", "response"),
                strict=bool(spec.get("strict", True)),
            )
        return None


def json_system_prompt(fmt: JsonFormat) -> str:
    if fmt.schema is None:
        return f"{JSON_MARK} Reply with one valid JSON object and nothing else."
    return (
        f"{JSON_MARK} Reply with one JSON value that matches this JSON Schema ({fmt.name}) and "
        "nothing else: no prose, no code fences.\n" + json.dumps(fmt.schema, ensure_ascii=False)
    )


def schema_errors(value: Any, schema: Mapping[str, Any]) -> list[str]:
    """Why ``value`` does not match ``schema`` (at most 5 reasons), or []."""
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
    except jsonschema.SchemaError as exc:
        return [f"the schema itself is invalid: {exc.message}"]
    errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
    out = []
    for error in errors[:5]:
        where = "/".join(str(p) for p in error.path) or "(root)"
        out.append(f"{where}: {error.message}")
    return out


def extract_json_value(text: str) -> tuple[Any, bool]:
    """(value, found): the answer's JSON, whole or fenced or embedded."""
    stripped = text.strip()
    try:
        return json.loads(stripped), True
    except ValueError:
        pass
    for match in _FENCED.finditer(text):
        try:
            return json.loads(match.group(1)), True
        except ValueError:
            continue
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value, True
            except ValueError:
                continue
    return None, False


def validate_json_answer(text: str, fmt: JsonFormat) -> tuple[list[str], str]:
    """(issues, the answer as clean JSON text)."""
    value, found = extract_json_value(text)
    if not found:
        return ["the answer is not valid JSON"], text
    if fmt.schema is None:
        if not isinstance(value, dict):
            return ["the answer must be a JSON object"], text
        return [], json.dumps(value, ensure_ascii=False)
    issues = schema_errors(value, fmt.schema)
    return issues, json.dumps(value, ensure_ascii=False)


# --- example values (demo models and tests) -----------------------------------------------------


def example_for(schema: Mapping[str, Any] | None) -> Any:
    """A value that matches a (simple) JSON schema: for demo models and tests."""
    schema = schema or {}
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return example_for(schema[key][0])
    kind = schema.get("type", "object")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        props = schema.get("properties") or {}
        return {name: example_for(sub) for name, sub in props.items()}
    if kind == "array":
        return [example_for(schema.get("items") or {})] * max(1, int(schema.get("minItems", 1)))
    if kind == "string":
        return "example" if "format" not in schema else "2026-01-01"
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1.0))
    if kind == "boolean":
        return True
    return None


@dataclass
class CallOutcome:
    """What a validation hook decided about one model reply."""

    issues: list[str] = field(default_factory=list)
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
