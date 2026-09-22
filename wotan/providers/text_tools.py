"""Textual tool-call protocol for models without native function calling.

We deliberately use delimiters that never appear in real code:

    <<<WOTAN_TOOL>>>
    {"name": "fs_read", "arguments": {"path": "main.py"}}
    <<<WOTAN_TOOL_END>>>

A strict primary parser plus a tolerant fallback parser (```tool_call fences,
bare ``{"tool_calls": [...]}`` JSON) with JSON repair for the usual mistakes
(single quotes, trailing commas, unquoted keys). Malformed calls are validated
against the tool's JSON Schema and answered with ERROR/WHY/HOW TO FIX so weak
models can self-correct.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

BEGIN = "<<<WOTAN_TOOL>>>"
END = "<<<WOTAN_TOOL_END>>>"

_BLOCK_RE = re.compile(re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.DOTALL)
_FENCE_RE = re.compile(r"```tool_call\s*\n(.*?)```", re.DOTALL)


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict[str, Any]
    raw: str
    repaired: bool = False


@dataclass
class ParseIssue:
    error: str
    why: str
    how_to_fix: str


def serialize_tool_call(name: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)
    return f"{BEGIN}\n{payload}\n{END}"


def _repair_json(text: str) -> tuple[Any, bool]:
    """Try increasingly tolerant repairs. Returns (obj, was_repaired)."""
    try:
        return json.loads(text), False
    except json.JSONDecodeError:
        pass
    candidates = []
    # Trailing commas.
    candidates.append(re.sub(r",\s*([}\]])", r"\1", text))
    # Single quotes -> double quotes (naive but effective for arguments).
    candidates.append(re.sub(r"(?<!\\)'", '"', candidates[-1]))
    # Unquoted object keys.
    candidates.append(re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_\-]*)\s*:", r'\1"\2":', candidates[-1]))
    # Python literals.
    candidates.append(candidates[-1].replace("None", "null").replace("True", "true").replace("False", "false"))
    for cand in candidates:
        try:
            return json.loads(cand), True
        except json.JSONDecodeError:
            continue
    # Last resort: extract the first {...} object.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0)), True
        except json.JSONDecodeError:
            pass
    raise ValueError("not valid JSON even after repair")


def _to_call(obj: Any, raw: str, repaired: bool) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    if isinstance(obj, dict) and "tool_calls" in obj and isinstance(obj["tool_calls"], list):
        items = obj["tool_calls"]
    elif isinstance(obj, dict) and "name" in obj:
        items = [obj]
    elif isinstance(obj, list):
        items = obj
    else:
        return calls
    for item in items:
        if not isinstance(item, dict):
            continue
        fn = item.get("function")
        name = item.get("name") or item.get("tool")
        if not name and isinstance(fn, dict):
            name = fn.get("name")
        if not name:
            continue
        args = item.get("arguments") if "arguments" in item else item.get("input", item.get("args", {}))
        if isinstance(args, str):
            try:
                parsed, rep = _repair_json(args)
                args = parsed if isinstance(parsed, dict) else {"value": parsed}
                repaired = repaired or rep
            except ValueError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"value": args}
        calls.append(ParsedToolCall(name=str(name), arguments=args, raw=raw, repaired=repaired))
    return calls


def _balanced_json_objects(text: str) -> list[str]:
    """Find balanced {...} regions (string-aware) - for bare tool_calls JSON."""
    out: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"' and not in_str:
            in_str = True
            continue
        if ch == '"' and in_str:
            in_str = False
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                out.append(text[start : i + 1])
                start = -1
    return out


def parse_tool_calls(text: str) -> tuple[list[ParsedToolCall], list[ParseIssue]]:
    """Extract tool calls from model output text. Never raises."""
    calls: list[ParsedToolCall] = []
    issues: list[ParseIssue] = []
    seen_spans: list[tuple[int, int]] = []

    blocks = [(m.start(), m.end(), m.group(1)) for m in _BLOCK_RE.finditer(text)]
    if not blocks:
        blocks = [(m.start(), m.end(), m.group(1)) for m in _FENCE_RE.finditer(text)]

    for start, end, body in blocks:
        seen_spans.append((start, end))
        body = body.strip()
        try:
            obj, repaired = _repair_json(body)
        except ValueError:
            issues.append(
                ParseIssue(
                    error="tool call block is not valid JSON",
                    why="the model output between the tool delimiters could not be parsed, even after repair",
                    how_to_fix=f"emit the call again as:\n{BEGIN}\n{{\"name\": \"tool_name\", \"arguments\": {{}}}}\n{END}",
                )
            )
            continue
        found = _to_call(obj, body, repaired)
        if not found:
            issues.append(
                ParseIssue(
                    error="tool call block has no 'name'",
                    why="JSON parsed but no tool name field (name/tool/function.name) was present",
                    how_to_fix='use {"name": "...", "arguments": {...}}',
                )
            )
        calls.extend(found)

    if not calls:
        # Bare {"tool_calls": [...]} without delimiters (very common with weak models):
        # scan balanced JSON objects and pick the ones that carry tool calls.
        for candidate in _balanced_json_objects(text):
            if '"tool_calls"' not in candidate and '"name"' not in candidate:
                continue
            try:
                obj, repaired = _repair_json(candidate)
            except ValueError:
                continue
            found = _to_call(obj, candidate, repaired)
            if found:
                calls.extend(found)
                break

    return calls, issues


def extract_reply_text(text: str) -> str:
    """The assistant text with tool-call blocks removed (for display/history)."""
    out = _BLOCK_RE.sub("", text)
    out = _FENCE_RE.sub("", out)
    return out.strip()


def validate_arguments(
    name: str, arguments: dict[str, Any], schema: dict[str, Any]
) -> ParseIssue | None:
    """Minimal JSON-Schema validation (type/required/enum) with fix hints."""
    props = (schema or {}).get("properties", {}) or {}
    required = (schema or {}).get("required", []) or []
    for req in required:
        if req not in arguments:
            return ParseIssue(
                error=f"tool '{name}' is missing required argument '{req}'",
                why=f"the JSON Schema for '{name}' marks '{req}' as required",
                how_to_fix=f"add \"{req}\" to the arguments object and call the tool again",
            )
    for key, value in arguments.items():
        spec = props.get(key)
        if not spec:
            if (schema or {}).get("additionalProperties") is False:
                return ParseIssue(
                    error=f"tool '{name}' has no argument '{key}'",
                    why=f"allowed arguments: {', '.join(sorted(props)) or '(none)'}",
                    how_to_fix="remove the unknown argument or check the tool description",
                )
            continue
        expected = spec.get("type")
        if expected:
            ok = {
                "string": isinstance(value, str),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
            }.get(expected, True)
            if not ok:
                return ParseIssue(
                    error=f"argument '{key}' of '{name}' should be {expected}",
                    why=f"got {type(value).__name__}",
                    how_to_fix=f"pass a {expected} value for '{key}'",
                )
        enum = spec.get("enum")
        if enum and value not in enum:
            return ParseIssue(
                error=f"argument '{key}' of '{name}' must be one of {enum}",
                why=f"got {value!r}",
                how_to_fix=f"use one of: {', '.join(str(e) for e in enum)}",
            )
    return None
