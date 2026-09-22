"""GenericHTTPProvider against the fictional mock contract (end to end)."""

from __future__ import annotations

import json

import pytest

from wotan.config import parse_config
from wotan.providers.base import Message, ToolSpec
from wotan.providers.generic_http import GenericHTTPProvider, extract_path
from wotan.providers.text_tools import BEGIN, END


def test_extract_path_dotted():
    data = {"a": {"b": [{"c": 42}, {"c": 7}]}, "x": "s"}
    assert extract_path(data, "a.b[0].c") == 42
    assert extract_path(data, "a.b[1].c") == 7
    assert extract_path(data, "x") == "s"
    assert extract_path(data, "$.a.b[0].c") == 42
    assert extract_path(data, "missing.path") is None
    assert extract_path(data, "$") is data


def test_extract_path_jsonpath_filter():
    data = {"items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
    val = extract_path(data, "$.items[?(@.id==2)].name")
    assert val in ("b", ["b"])


def test_extract_path_jmespath():
    data = {"a": {"b": "found"}}
    assert extract_path(data, "a.b") == "found"


def _provider(mock_identity_cfg) -> GenericHTTPProvider:
    cfg = parse_config({"providers": [mock_identity_cfg]})
    return GenericHTTPProvider(cfg.providers[0])


async def test_chat_round_trip_fictional_contract(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    result = await p.chat([Message(role="user", content="hello there")], model="mock-chat")
    assert "echo: hello there" in result.text
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.stop_reason == "stop"
    await p.aclose()


async def test_native_tool_calls_fictional_contract(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    result = await p.chat([Message(role="user", content="CALL_TOOL please")], model="mock-chat",
                          tools=[ToolSpec(name="fs_read", description="d", parameters={"type": "object", "properties": {}})])
    assert result.tool_calls
    assert result.tool_calls[0].name == "fs_read"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.stop_reason == "actions"
    await p.aclose()


async def test_textual_tool_mode(mock_identity_cfg):
    cfg = dict(mock_identity_cfg)
    cfg["chat"] = dict(cfg["chat"], native_tools=False)
    p = _provider(cfg)
    result = await p.chat([Message(role="user", content="CALL_TOOL please")], model="mock-weak")
    assert result.tool_calls
    assert result.tool_calls[0].name == "fs_read"
    assert "<<<WOTAN_TOOL" not in result.text
    await p.aclose()


async def test_streaming_sse(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    chunks: list[str] = []

    async def on_event(ev):
        if ev.type == "text_delta":
            chunks.append(ev.text)

    result = await p.chat([Message(role="user", content="STREAM please")], model="mock-chat",
                          stream=True, on_event=on_event)
    assert chunks
    assert "Streaming works" in "".join(chunks)
    await p.aclose()


async def test_error_extraction_path(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    with pytest.raises(Exception) as exc:
        await p.chat([Message(role="user", content="HTTP500 fail")], model="mock-chat")
    assert "error" in str(exc.value).lower() or "500" in str(exc.value)
    await p.aclose()


async def test_401_retries_after_refresh(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    # poison the cached token, then chat -> provider refreshes and retries
    await p.tokens.get_token()
    p.tokens._info = None
    p.tokens._info = None
    from wotan.auth.token_manager import TokenInfo

    p.tokens._info = TokenInfo(token="stale-token", expires_at=None)
    result = await p.chat([Message(role="user", content="hello")], model="mock-chat")
    assert "echo: hello" in result.text
    await p.aclose()


async def test_list_models_from_endpoint(mock_identity_cfg):
    p = _provider(mock_identity_cfg)
    models = await p.list_models()
    ids = {m.id for m in models}
    assert "mock-chat" in ids and "mock-vision" in ids
    await p.aclose()


async def test_role_map_applied(mock_identity_cfg):
    seen: dict = {}
    cfg = dict(mock_identity_cfg)
    chat = dict(cfg["chat"])
    chat["body_template"] = (
        '{"model_id": "{{ model }}", "instances": [{"input_text": {{ messages | tojson }}}], "params": {}}'
    )
    cfg["chat"] = chat
    p = _provider(cfg)
    # inspect the built body
    body = p._build_body([Message(role="system", content="SYS"), Message(role="user", content="USR")], None, "mock-chat", 0.2, 100)
    inst = body["instances"][0]["input_text"]
    roles = [m["role"] for m in inst]
    assert "instruction" in roles and "human" in roles  # system->instruction, user->human
    await p.aclose()


async def test_jinja_template_with_tools(mock_identity_cfg):
    cfg = dict(mock_identity_cfg)
    chat = dict(cfg["chat"])
    chat["body_template"] = '{"model_id": "{{ model }}", "instances": [{"input_text": "{{ messages[0].content }}"}], "tool_names": {{ tools | tojson if tools else [] }}, "params": {"t": {{ temperature }}}}'
    cfg["chat"] = chat
    p = _provider(cfg)
    body = p._build_body([Message(role="user", content="hi")], [ToolSpec(name="t1", description="d", parameters={})], "m", 0.5, 10)
    assert body["params"]["t"] == 0.5
    assert any(x.get("function", {}).get("name") == "t1" for x in body["tool_names"])
    await p.aclose()
