"""Agent loop with a scripted fake provider: tool dispatch, streaming events,
finish_task gate integration, stop button, plan mode restrictions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wotan.agent.session import AgentSession
from wotan.config import AppConfig, parse_config
from wotan.providers.base import ChatResult, LLMProvider, Message, ModelInfo, ToolCall, Usage


class FakeProvider(LLMProvider):
    """Scripted provider: each chat() call returns the next scripted result."""

    id = "fake"
    name = "Fake"

    def __init__(self, script: list[ChatResult] | list[dict[str, Any]]) -> None:
        self.script: list[ChatResult] = []
        for item in script:
            if isinstance(item, ChatResult):
                self.script.append(item)
            else:
                calls = [
                    ToolCall(id=f"c{i}", name=c["name"], arguments=c.get("arguments", {}))
                    for i, c in enumerate(item.get("tool_calls", []))
                ]
                self.script.append(ChatResult(text=item.get("text", ""), tool_calls=calls, stop_reason="stop", usage=Usage(10, 5)))
        self.calls: list[list[Message]] = []
        self.tools_seen: list[list[str]] = []

    async def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=None, stream=None, on_event=None):
        self.calls.append(list(messages))
        self.tools_seen.append([t.name for t in (tools or [])])
        result = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if on_event and result.text:
            await on_event(type("E", (), {"type": "text_delta", "text": result.text, "tool_call": None, "usage": None, "stop_reason": "", "raw": None})())
        return result


class FakeRegistry:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def resolve(self, model_ref: str = ""):
        return self.provider, "fake-model"


@pytest.fixture
def events():
    collected: list[dict[str, Any]] = []

    async def emit(e: dict[str, Any]) -> None:
        collected.append(e)

    return collected, emit


@pytest.fixture
def agent(ws: Path, db, events):
    collected, emit = events
    cfg = parse_config({"permissions": {"default_mode": "autonomous"}, "verification": {"on_stop_hook": False}})
    session = AgentSession(cfg, ws, db, emit)
    return session, collected


async def test_text_only_turn(agent):
    session, collected = agent
    fake = FakeProvider([{"text": "Hello! I am done thinking."}])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    result = await session.run_turn("hi")
    assert result["status"] == "done"
    assert "Hello" in result["text"]
    assert any(e["type"] == "token" for e in collected)
    assert any(e["type"] == "usage" for e in collected)
    assert collected[-1]["type"] == "done"


async def test_tool_calling_loop(agent, ws: Path):
    session, collected = agent
    (ws / "f.py").write_text("x = 1\n", encoding="utf-8")
    fake = FakeProvider([
        {"text": "Let me edit.", "tool_calls": [{"name": "fs_read", "arguments": {"path": "f.py"}}]},
        {"text": "Now editing.", "tool_calls": [{"name": "fs_edit", "arguments": {"path": "f.py", "old_string": "x = 1", "new_string": "x = 2"}}]},
        {"text": "Done - the file now has x = 2."},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    result = await session.run_turn("change x")
    assert result["status"] == "done"
    assert (ws / "f.py").read_text(encoding="utf-8") == "x = 2\n"
    tool_events = [e for e in collected if e["type"] == "tool_end"]
    assert [e["tool"] for e in tool_events] == ["fs_read", "fs_edit"]
    # dirty flag was set by the edit
    assert any(e["type"] == "dirty" for e in collected)


async def test_tool_error_message_reaches_model(agent, ws: Path):
    session, collected = agent
    fake = FakeProvider([
        {"text": "trying", "tool_calls": [{"name": "fs_edit", "arguments": {"path": "missing.py", "old_string": "a", "new_string": "b"}}]},
        {"text": "gave up"},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    await session.run_turn("edit")
    tool_msgs = [m for m in session.history if m.role == "tool"]
    assert tool_msgs
    assert "error" in tool_msgs[0].content


async def test_finish_task_gate_blocks_without_evidence(agent):
    session, collected = agent
    fake = FakeProvider([
        {"text": "done!", "tool_calls": [{"name": "finish_task", "arguments": {"summary": "all done", "acceptance_criteria": [], "commands": []}}]},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    result = await session.run_turn("task")
    verdicts = [e for e in collected if e["type"] == "finish_verdict"]
    assert verdicts
    assert verdicts[0]["finished"] is False  # no real verification was run


async def test_finish_task_gate_accepts_with_real_runs(agent, ws: Path):
    session, collected = agent

    async def run_and_finish(*_a, **_k):
        return None

    fake = FakeProvider([
        {"text": "verifying", "tool_calls": [{"name": "shell_exec", "arguments": {"command": "python -c \"print('ok')\""}}]},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]

    # First turn: run the verification command
    await session.run_turn("verify")
    runs = session.exec_log.list(session_id=session.session_id)
    assert runs and runs[0].exit_code == 0
    rid = runs[0].id

    # Second turn: finish with matching evidence
    fake2 = FakeProvider([
        {"text": "finishing", "tool_calls": [{"name": "finish_task", "arguments": {
            "summary": "verified",
            "acceptance_criteria": [{"criterion": "script runs", "status": "passed", "run_id": rid}],
            "commands": [{"run_id": rid, "command": runs[0].command, "exit_code": 0}],
        }}]},
    ])
    session.registry = FakeRegistry(fake2)  # type: ignore[assignment]
    session.gate.mark_dirty("x")  # simulate edit after verification
    await session.run_turn("finish")
    verdicts = [e for e in collected if e["type"] == "finish_verdict"]
    assert verdicts[-1]["finished"] is False  # dirty since last verification

    # verify again then finish clean
    session.gate.note_verification_success([rid])
    fake3 = FakeProvider([
        {"text": "finishing", "tool_calls": [{"name": "finish_task", "arguments": {
            "summary": "verified",
            "acceptance_criteria": [{"criterion": "script runs", "status": "passed", "run_id": rid}],
            "commands": [{"run_id": rid, "command": runs[0].command, "exit_code": 0}],
        }}]},
    ])
    session.registry = FakeRegistry(fake3)  # type: ignore[assignment]
    await session.run_turn("finish again")
    verdicts = [e for e in collected if e["type"] == "finish_verdict"]
    assert verdicts[-1]["finished"] is True


async def test_plan_mode_restricts_tools(agent):
    session, collected = agent
    fake = FakeProvider([{"text": "Plan: 1. do things"}])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    await session.run_turn("plan this", agent_mode="Plan")
    assert "fs_edit" not in fake.tools_seen[0]
    assert "fs_read" in fake.tools_seen[0]
    assert "finish_task" not in fake.tools_seen[0]


async def test_textual_tool_calls_from_weak_model(agent, ws: Path):
    session, collected = agent
    (ws / "t.txt").write_text("hello\n", encoding="utf-8")
    from wotan.providers.text_tools import BEGIN, END

    text = (
        'Reading now.\n'
        f'{BEGIN}\n{{"name": "fs_read", "arguments": {{"path": "t.txt"}}}}\n{END}\n'
    )
    fake = FakeProvider([
        {"text": text},
        {"text": "I saw the content."},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    result = await session.run_turn("read")
    tool_events = [e for e in collected if e["type"] == "tool_end"]
    assert tool_events and tool_events[0]["tool"] == "fs_read"
    assert result["status"] == "done"


async def test_step_limit(agent):
    session, collected = agent
    session.config.limits.max_steps = 3
    fake = FakeProvider([
        {"text": "loop", "tool_calls": [{"name": "fs_list", "arguments": {}}]},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    result = await session.run_turn("loop forever")
    assert result["status"] == "limit_reached"


async def test_ask_user_roundtrip(agent):
    session, collected = agent
    fake = FakeProvider([
        {"text": "asking", "tool_calls": [{"name": "ask_user", "arguments": {"question": "which port?"}}]},
        {"text": "Using port 8080."},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]

    async def auto_reply():
        import asyncio

        for _ in range(100):
            await asyncio.sleep(0.02)
            asks = [e for e in collected if e["type"] == "ask_user"]
            if asks:
                await session.resolve_prompt_reply(asks[0]["id"], "8080")
                return

    import asyncio

    task = asyncio.create_task(auto_reply())
    result = await session.run_turn("deploy")
    await task
    assert result["status"] == "done"
    assert "8080" in result["text"]


async def test_mcp_external_workflow_tool(agent, ws: Path, mock_server, mock_identity_cfg):
    """External workflows registered from YAML become agent tools."""
    from wotan.config import parse_config as pc

    cfg_data = {
        "permissions": {"default_mode": "autonomous"},
        "providers": [mock_identity_cfg],
        "external_tools": [{
            "name": "ticket_lookup", "description": "Lookup", "endpoint": "/mock/api/workflows/ticket_lookup",
            "provider_id": "fictional", "input_template": "{{ inputs | tojson }}", "result_path": "output",
        }],
    }
    cfg = pc(cfg_data)
    collected, emit = (agent[1], None)
    session = AgentSession(cfg, ws, agent[0].db, agent[0].emit)
    fake = FakeProvider([
        {"text": "calling", "tool_calls": [{"name": "ticket_lookup", "arguments": {"ticket": "T-9"}}]},
        {"text": "done"},
    ])
    session.registry = FakeRegistry(fake)  # type: ignore[assignment]
    await session.run_turn("look up ticket")
    tool_msgs = [m for m in session.history if m.role == "tool"]
    assert tool_msgs
    assert "ticket_lookup" in tool_msgs[0].name or "T-9" in tool_msgs[0].content
