"""Textual tool-call protocol: strict parsing, tolerant repair, schema errors."""

from __future__ import annotations

import json

from wotan.providers.text_tools import (
    BEGIN,
    END,
    extract_reply_text,
    parse_tool_calls,
    serialize_tool_call,
    validate_arguments,
)


def test_serialize_roundtrip():
    text = serialize_tool_call("fs_read", {"path": "a.py"})
    calls, issues = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "fs_read"
    assert calls[0].arguments == {"path": "a.py"}
    assert not issues


def test_parse_extracts_and_strips_from_reply():
    text = f"I will read the file.\n{BEGIN}\n{{\"name\": \"fs_read\", \"arguments\": {{\"path\": \"x\"}}}}\n{END}\nDone."
    calls, _ = parse_tool_calls(text)
    assert calls[0].name == "fs_read"
    reply = extract_reply_text(text)
    assert BEGIN not in reply
    assert "read the file" in reply


def test_parse_fence_format():
    text = '```tool_call\n{"name": "shell_exec", "arguments": {"command": "ls"}}\n```'
    calls, _ = parse_tool_calls(text)
    assert calls[0].name == "shell_exec"


def test_parse_bare_tool_calls_json():
    text = 'Sure: {"tool_calls": [{"name": "todo_write", "arguments": {"todos": []}}]}'
    calls, _ = parse_tool_calls(text)
    assert calls and calls[0].name == "todo_write"


def test_repair_single_quotes():
    body = "{'name': 'fs_read', 'arguments': {'path': 'a.py'}}"
    text = f"{BEGIN}\n{body}\n{END}"
    calls, _ = parse_tool_calls(text)
    assert calls[0].arguments["path"] == "a.py"
    assert calls[0].repaired


def test_repair_trailing_comma_and_unquoted_keys():
    body = '{name: "fs_read", arguments: {path: "a.py",},}'
    text = f"{BEGIN}\n{body}\n{END}"
    calls, _ = parse_tool_calls(text)
    assert calls[0].name == "fs_read"


def test_repair_arguments_as_string():
    body = '{"name": "fs_read", "arguments": "{\\"path\\": \\"a.py\\"}"}'
    text = f"{BEGIN}\n{body}\n{END}"
    calls, _ = parse_tool_calls(text)
    assert calls[0].arguments == {"path": "a.py"}


def test_openai_style_name_nested():
    body = '{"tool_calls": [{"function": {"name": "fs_read", "arguments": "{}"}}]}'
    calls, _ = parse_tool_calls(body)
    assert calls[0].name == "fs_read"


def test_garbage_block_yields_issue():
    text = f"{BEGIN}\nthis is not json at all\n{END}"
    calls, issues = parse_tool_calls(text)
    assert not calls
    assert issues and "HOW TO FIX" in issues[0].how_to_fix or "<<<WOTAN_TOOL>>>" in issues[0].how_to_fix


def test_validate_arguments_required():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    issue = validate_arguments("fs_read", {}, schema)
    assert issue and "missing required" in issue.error
    issue = validate_arguments("fs_read", {"path": 123}, schema)
    assert issue and "should be string" in issue.error
    issue = validate_arguments("fs_read", {"path": "x"}, schema)
    assert issue is None


def test_validate_arguments_enum_and_unknown():
    schema = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["a", "b"]}}, "additionalProperties": False}
    assert validate_arguments("t", {"mode": "c"}, schema) is not None
    assert validate_arguments("t", {"nope": 1}, schema) is not None
    assert validate_arguments("t", {"mode": "a"}, schema) is None
