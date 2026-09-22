"""Agent tools: fs tools, sensitive files, shell output contract, aliases."""

from __future__ import annotations

from pathlib import Path

import pytest

from wotan.tools import ToolContext, build_registry


@pytest.fixture
def ctx(ws: Path, engine) -> ToolContext:
    return ToolContext(workspace=ws, edit_engine=engine, session_id="s1")


@pytest.fixture
def reg():
    return build_registry()


async def test_aliases_resolve(reg, ctx):
    (ws_file := ctx.workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = await reg.dispatch(ctx, "read_file", {"path": "a.py"})  # alias of fs_read
    assert out["status"] == "ok"
    assert "x = 1" in out["content"]


async def test_unknown_tool_error_mentions_alternatives(reg, ctx):
    out = await reg.dispatch(ctx, "fs_edti", {"path": "x"})
    assert out["status"] == "error"
    assert "HOW TO FIX" in out["how_to_fix"] or "use one of" in out["how_to_fix"]


async def test_list_glob_grep(reg, ctx):
    (ctx.workspace / "src").mkdir()
    (ctx.workspace / "src" / "m.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (ctx.workspace / "README.md").write_text("alpha beta\n", encoding="utf-8")
    out = await reg.dispatch(ctx, "fs_list", {"path": "."})
    names = [e["name"] for e in out["entries"]]
    assert "src" in names and "README.md" in names
    out = await reg.dispatch(ctx, "glob", {"pattern": "**/*.py"})
    assert out["matches"] == ["src/m.py"]
    out = await reg.dispatch(ctx, "grep", {"pattern": "alpha"})
    paths = [f["path"] for f in out["files"]]
    assert "src/m.py" in paths and "README.md" in paths
    assert "2 files with matches" in out["summary"]


async def test_edit_tool_flow(reg, ctx):
    (ctx.workspace / "b.py").write_text("x = 1\n", encoding="utf-8")
    await reg.dispatch(ctx, "fs_read", {"path": "b.py"})
    out = await reg.dispatch(ctx, "edit_file", {"path": "b.py", "old_string": "x = 1", "new_string": "x = 2"})
    assert out["status"] == "ok"
    assert "x = 2" in out["snippet"]
    out = await reg.dispatch(ctx, "multi_edit", {"path": "b.py", "edits": [{"old_string": "x = 2", "new_string": "x = 3"}]})
    assert out["status"] == "ok"
    assert (ctx.workspace / "b.py").read_text(encoding="utf-8") == "x = 3\n"


async def test_write_tool_and_refusal(reg, ctx):
    out = await reg.dispatch(ctx, "write_file", {"path": "new.txt", "content": "hello\n"})
    assert out["status"] == "ok"
    body = "\n".join(f"line{i}" for i in range(60))
    (ctx.workspace / "big.txt").write_text(body + "\n", encoding="utf-8")
    await reg.dispatch(ctx, "fs_read", {"path": "big.txt", "limit": 300})
    out = await reg.dispatch(ctx, "write_file", {"path": "big.txt", "content": body.replace("line59", "X") + "\n"})
    assert out["status"] == "error"


async def test_sensitive_file_refused(reg, ctx):
    (ctx.workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")
    out = await reg.dispatch(ctx, "fs_read", {"path": ".env"})
    assert out["status"] == "error"
    assert "sensitive" in out["error"]


async def test_path_escape_refused(reg, ctx):
    out = await reg.dispatch(ctx, "fs_read", {"path": "../outside.txt"})
    assert out["status"] == "error"


async def test_shell_no_output_contract(reg, ctx, monkeypatch):
    async def fake_execute(**kw):
        from wotan.tools import ExecResult

        return ExecResult(exit_code=0, stdout="", stderr="")

    ctx.execute = fake_execute
    out = await reg.dispatch(ctx, "bash", {"command": "true"})
    assert out["status"] == "ok"
    assert "ran successfully, no output" in out["output"]


async def test_shell_error_why_how(reg, ctx):
    async def fake_execute(**kw):
        from wotan.tools import ExecResult

        return ExecResult(exit_code=1, stdout="", stderr="boom")

    ctx.execute = fake_execute
    out = await reg.dispatch(ctx, "shell_exec", {"command": "fail"})
    assert "[exit code: 1]" in out["output"]


async def test_todo_write_sink(reg, ctx):
    seen = []
    ctx.todo_sink = lambda t: seen.extend(t)
    out = await reg.dispatch(ctx, "todo_write", {"todos": [{"text": "one", "status": "pending"}]})
    assert out["status"] == "ok"
    assert seen and seen[0]["text"] == "one"


async def test_read_app_logs_and_search_sessions(reg, ctx):
    ctx.read_logs = lambda **kw: [{"message": "hello"}]
    ctx.search_sessions = lambda q: [{"ref": "m-1"}]
    out = await reg.dispatch(ctx, "read_app_logs", {"limit": 10})
    assert out["entries"]
    out = await reg.dispatch(ctx, "search_sessions", {"query": "x"})
    assert out["hits"]


async def test_run_python_writes_script(reg, ctx):
    ran = {}

    async def fake_execute(**kw):
        from wotan.tools import ExecResult

        ran.update(kw)
        return ExecResult(exit_code=0, stdout="ok")

    ctx.execute = fake_execute
    out = await reg.dispatch(ctx, "run_python", {"code": "print('hi')"})
    assert out["status"] == "ok"
    assert "python" in ran["command"]


async def test_empty_command_rejected(reg, ctx):
    out = await reg.dispatch(ctx, "bash", {"command": "   "})
    assert out["status"] == "error"
