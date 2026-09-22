"""Post-edit validators: markers, placeholders, syntax, emoji, mojibake, diff."""

from __future__ import annotations

from wotan.editing.validators import (
    check_emoji_policy,
    check_mojibake,
    check_new_markers,
    check_syntax,
    diff_sanity,
    scan_markers,
)
from wotan.security import fix_mojibake, looks_like_mojibake, scan_secrets


def test_markers_found():
    hits = scan_markers("hello\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n")
    assert any(k == "merge_conflict" for k, _ in hits)


def test_new_markers_ok_when_not_introduced():
    before = "x\n<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> b\n"
    r = check_new_markers(before, before, "python")
    assert r.ok


def test_new_markers_rejected_when_introduced():
    r = check_new_markers("x = 1\n", "x = 1\n<<<<<<< HEAD\n", "python")
    assert not r.ok


def test_placeholder_detection():
    r = check_new_markers("x=1\n", "x=1\n# ... existing code ...\n", "python")
    assert not r.ok
    r = check_new_markers("x=1\n", "x=1\n// rest unchanged\n", "javascript")
    assert not r.ok
    r = check_new_markers("x=1\n", "x=1\n... rest of the code ...\n", "python")
    assert not r.ok


def test_tool_delimiter_rejected():
    r = check_new_markers("x=1\n", "x=1\n<<<WOTAN_TOOL>>>\n", "python")
    assert not r.ok


def test_syntax_python():
    assert check_syntax("def f():\n    pass\n", "python").ok
    assert not check_syntax("def f(:\n", "python").ok


def test_syntax_json_yaml_toml():
    assert check_syntax('{"a": 1}', "json").ok
    assert not check_syntax('{"a": 1,}', "json").ok
    assert check_syntax("a: 1\n", "yaml").ok
    assert not check_syntax("a: [1\n", "yaml").ok
    assert check_syntax('[x]\ny = 1\n', "toml").ok


def test_syntax_js_brackets():
    assert check_syntax("function f() { return (1 + 2); }\n", "javascript").ok
    assert not check_syntax("function f() { return (1 + 2;\n", "javascript").ok
    assert check_syntax('const s = "(()";\n', "javascript").ok  # string content ignored


def test_diff_sanity_mass_deletion():
    before = "\n".join(f"x{i}" for i in range(200))
    r = diff_sanity(before + "\n", "x0\nx1\n", "x0\nx1", "x0\nx1", requested_removal=False)
    assert not r.ok


def test_diff_sanity_ok_for_normal_edit():
    before = "\n".join(f"x{i}" for i in range(50))
    after = before.replace("x25", "CHANGED")
    r = diff_sanity(before + "\n", after + "\n", "x25", "CHANGED")
    assert r.ok


def test_diff_sanity_truncation():
    before = "\n".join(f"x{i}" for i in range(50)) + "\n"
    after = "\n".join(f"x{i}" for i in range(10)) + "\n... rest unchanged ...\n"
    r = diff_sanity(before, after, "a", "b")
    assert not r.ok or scan_markers(after)  # truncated or placeholder


def test_emoji_policy():
    r = check_emoji_policy("done ✅", True)
    assert not r.ok
    r = check_emoji_policy("done ✅", False)
    assert r.ok
    r = check_emoji_policy("done [OK]", True)
    assert r.ok


def test_mojibake_detection_and_fix():
    bad = "cafÃ© com aÃ§Ãºcar"
    assert looks_like_mojibake(bad)
    fixed, changed = fix_mojibake(bad)
    assert changed
    assert fixed == "café com açúcar"
    assert check_mojibake(bad).warnings
    good = "café com açúcar"
    assert not looks_like_mojibake(good)
    assert fix_mojibake(good) == (good, False)


def test_secret_scanning():
    findings = scan_secrets('api_key = "sk-abcdef123456789012345678" and token')
    assert findings and findings[0].kind == "openai_style_key"
    findings = scan_secrets("Authorization: Bearer abcdefghijklmnop")
    assert findings
    assert not scan_secrets("print('hello world')")
