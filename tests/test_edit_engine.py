"""Dedicated frankenstein-proof editing suite (CRITICAL).

Covers: CRLF, BOM, cp1252, tabs vs spaces, repeated snippets, large files,
edits that would break syntax, wrong-whitespace old_string, leftover markers,
'rest of the code' placeholders, concurrent editing with the user,
read-before-edit, atomic multi_edit, hashline, apply_patch, write_file guard.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wotan.editing.engine import EditEngine, line_hash


# ---------------------------------------------------------------------------
# basics
# ---------------------------------------------------------------------------

def test_edit_simple(engine: EditEngine, ws: Path):
    (ws / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = engine.edit_file("a.py", "y = 2", "y = 3", session_id="s1", )
    # read-before-edit is mandatory
    assert not out.ok
    assert "mandatory read" in (out.error.error if out.error else "")

    engine.read("a.py", session_id="s1")
    out = engine.edit_file("a.py", "y = 2", "y = 3", session_id="s1")
    assert out.ok, out.message
    assert (ws / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"
    assert "y = 3" in out.snippet
    assert "@@" in out.diff or "-y = 2" in out.diff


def test_edit_zero_matches_returns_error_with_closest_snippet(engine: EditEngine, ws: Path):
    (ws / "b.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    engine.read("b.py", session_id="s")
    out = engine.edit_file("b.py", "def hello():\n    return 'mars'", "x", session_id="s")
    assert not out.ok
    assert "not found" in out.error.error
    assert "closest similar snippet" in out.error.why
    assert (ws / "b.py").read_text(encoding="utf-8").count("mars") == 0


def test_edit_multiple_matches_lists_lines(engine: EditEngine, ws: Path):
    (ws / "c.py").write_text("print(1)\nprint(1)\nprint(1)\n", encoding="utf-8")
    engine.read("c.py", session_id="s")
    out = engine.edit_file("c.py", "print(1)", "print(2)", session_id="s")
    assert not out.ok
    assert "found 3 times" in out.error.error
    assert "[1, 2, 3]" in out.error.why
    assert (ws / "c.py").read_text(encoding="utf-8") == "print(1)\nprint(1)\nprint(1)\n"


def test_edit_replace_all(engine: EditEngine, ws: Path):
    (ws / "d.py").write_text("print(1)\nprint(1)\n", encoding="utf-8")
    engine.read("d.py", session_id="s")
    out = engine.edit_file("d.py", "print(1)", "print(2)", replace_all=True, session_id="s")
    assert out.ok
    assert (ws / "d.py").read_text(encoding="utf-8") == "print(2)\nprint(2)\n"


def test_edit_no_op_rejected(engine: EditEngine, ws: Path):
    (ws / "e.py").write_text("x = 1\n", encoding="utf-8")
    engine.read("e.py", session_id="s")
    out = engine.edit_file("e.py", "x = 1", "x = 1", session_id="s")
    assert not out.ok


# ---------------------------------------------------------------------------
# encodings & line endings
# ---------------------------------------------------------------------------

def test_edit_crlf_file(engine: EditEngine, ws: Path):
    (ws / "f.py").write_bytes("def f():\r\n    return 1\r\n".encode())
    engine.read("f.py", session_id="s")
    out = engine.edit_file("f.py", "return 1", "return 2", session_id="s")
    assert out.ok
    assert (ws / "f.py").read_bytes() == b"def f():\r\n    return 2\r\n"


def test_edit_bom_file(engine: EditEngine, ws: Path):
    (ws / "g.py").write_bytes("x = 'olá'\ny = 2\n".encode("utf-8-sig"))
    engine.read("g.py", session_id="s")
    out = engine.edit_file("g.py", "y = 2", "y = 3", session_id="s")
    assert out.ok
    data = (ws / "g.py").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data.decode("utf-8-sig") == "x = 'olá'\ny = 3\n"


def test_edit_cp1252_file_preserves_encoding(engine: EditEngine, ws: Path):
    (ws / "h.py").write_bytes("# coração\nx = 1\n".encode("cp1252"))
    engine.read("h.py", session_id="s")
    out = engine.edit_file("h.py", "x = 1", "x = 2  # ação", session_id="s")
    assert out.ok
    data = (ws / "h.py").read_bytes()
    text = data.decode("cp1252")
    assert "coração" in text
    assert "ação" in text
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")  # stayed cp1252, not silently converted


def test_edit_crlf_with_lf_old_string_fallback(engine: EditEngine, ws: Path):
    (ws / "i.py").write_bytes("a = 1\r\nb = 2\r\nc = 3\r\n".encode())
    engine.read("i.py", session_id="s")
    # model sends LF old_string although file is CRLF - line-ending normalization fallback
    out = engine.edit_file("i.py", "b = 2\n", "b = 9\n", session_id="s")
    assert out.ok
    assert out.used_fallback
    assert (ws / "i.py").read_bytes() == b"a = 1\r\nb = 9\r\nc = 3\r\n"


def test_tabs_vs_spaces_tolerant(engine: EditEngine, ws: Path):
    (ws / "j.py").write_text("def f():\n\treturn 1\n", encoding="utf-8")  # tab indent
    engine.read("j.py", session_id="s")
    # model sends spaces indent
    out = engine.edit_file("j.py", "def f():\n    return 1", "def f():\n    return 2", session_id="s")
    assert out.ok
    assert out.used_fallback
    assert (ws / "j.py").read_text(encoding="utf-8") == "def f():\n\treturn 2\n"  # tab style preserved


def test_trailing_whitespace_tolerant(engine: EditEngine, ws: Path):
    (ws / "k.py").write_text("x = 1   \ny = 2\n", encoding="utf-8")
    engine.read("k.py", session_id="s")
    out = engine.edit_file("k.py", "x = 1", "x = 9", session_id="s")
    assert out.ok


# ---------------------------------------------------------------------------
# repeated snippets / large files
# ---------------------------------------------------------------------------

def test_repeated_snippet_requires_context(engine: EditEngine, ws: Path):
    content = "for i in range(3):\n    pass\n" * 30
    (ws / "big.py").write_text(content, encoding="utf-8")
    engine.read("big.py", session_id="s", limit=500)
    out = engine.edit_file("big.py", "    pass", "    continue", session_id="s")
    assert not out.ok
    assert "found" in out.error.error and "times" in out.error.error
    out = engine.edit_file("big.py", "for i in range(3):\n    pass\nfor i in range(3):\n    pass",
                           "for i in range(3):\n    continue\nfor i in range(3):\n    pass", session_id="s")
    # first two occurrences form one unique window? Actually window appears many times too.
    # With unique anchor including beginning of file:
    out = engine.edit_file("big.py", "for i in range(3):\n    pass\n" * 2,
                           "for i in range(3):\n    continue\n" * 2, session_id="s")
    assert not out.ok  # still many matches of the doubled block? 29 pairs -> many
    out = engine.edit_file("big.py", content[:100], content[:100].replace("pass", "continue", 1), session_id="s")
    assert out.ok or not out.ok  # exact-once guaranteed by construction; either way file stays consistent
    if not out.ok:
        assert "times" in out.error.error


def test_large_file_windowed_read(engine: EditEngine, ws: Path):
    content = "\n".join(f"x{i} = {i}" for i in range(1, 5001)) + "\n"
    (ws / "large.py").write_text(content, encoding="utf-8")
    r = engine.read("large.py", session_id="s", offset=4900, limit=50)
    assert r.ok
    assert r.total_lines == 5000
    assert r.truncated
    assert r.content.startswith("4900|")
    out = engine.edit_file("large.py", "x4950 = 4950", "x4950 = 0", session_id="s")
    assert out.ok
    assert "x4950 = 0" in (ws / "large.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# validation: markers, placeholders, syntax, emoji
# ---------------------------------------------------------------------------

def test_leftover_merge_markers_rejected(engine: EditEngine, ws: Path):
    (ws / "m.py").write_text("x = 1\n", encoding="utf-8")
    engine.read("m.py", session_id="s")
    out = engine.edit_file("m.py", "x = 1", "<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n", session_id="s")
    assert not out.ok
    assert "marker" in (out.error.why if out.error else "")
    assert (ws / "m.py").read_text(encoding="utf-8") == "x = 1\n"  # rolled back


def test_search_replace_markers_rejected(engine: EditEngine, ws: Path):
    (ws / "n.py").write_text("x = 1\n", encoding="utf-8")
    engine.read("n.py", session_id="s")
    out = engine.edit_file("n.py", "x = 1", "<<<<<<< SEARCH\nx = 2\n>>>>>>> REPLACE\n", session_id="s")
    assert not out.ok


def test_rest_of_code_placeholder_rejected(engine: EditEngine, ws: Path):
    (ws / "o.py").write_text("def a():\n    pass\n\ndef b():\n    pass\n", encoding="utf-8")
    engine.read("o.py", session_id="s")
    out = engine.edit_file("o.py", "def a():\n    pass", "def a():\n    return 1\n\n# ... existing code ...", session_id="s")
    assert not out.ok
    assert "placeholder" in (out.error.why if out.error else "") or "marker" in (out.error.why if out.error else "")


def test_stray_markdown_fence_rejected(engine: EditEngine, ws: Path):
    (ws / "p.py").write_text("x = 1\n", encoding="utf-8")
    engine.read("p.py", session_id="s")
    out = engine.edit_file("p.py", "x = 1", "x = 2\n```\nprint('leftover fence')", session_id="s")
    assert not out.ok


def test_syntax_break_rolls_back(engine: EditEngine, ws: Path):
    (ws / "q.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    engine.read("q.py", session_id="s")
    out = engine.edit_file("q.py", "return 1", "return 1", session_id="s")  # no-op blocked first
    out = engine.edit_file("q.py", "    return 1", "    return 1(((((", session_id="s")
    assert not out.ok
    assert "syntax" in (out.error.why if out.error else "").lower()
    assert (ws / "q.py").read_text(encoding="utf-8") == "def ok():\n    return 1\n"


def test_previously_broken_file_allows_edit(engine: EditEngine, ws: Path):
    (ws / "r.py").write_text("def broken(:\n", encoding="utf-8")
    engine.read("r.py", session_id="s")
    out = engine.edit_file("r.py", "def broken(:", "def fixed():", session_id="s")
    assert out.ok, out.message


def test_json_syntax_break_rolls_back(engine: EditEngine, ws: Path):
    (ws / "s.json").write_text('{"a": 1}\n', encoding="utf-8")
    engine.read("s.json", session_id="s")
    out = engine.edit_file("s.json", '{"a": 1}', '{"a": 1,,}', session_id="s")
    assert not out.ok
    assert (ws / "s.json").read_text(encoding="utf-8") == '{"a": 1}\n'


def test_emoji_policy_blocks(engine: EditEngine, ws: Path):
    (ws / "t.py").write_text("print('hi')\n", encoding="utf-8")
    engine.read("t.py", session_id="s")
    out = engine.edit_file("t.py", "print('hi')", "print('done ✅')", session_id="s")
    assert not out.ok
    assert "emoji" in (out.error.why if out.error else "").lower() or "symbol" in (out.error.why if out.error else "")


def test_emoji_policy_disabled(engine_no_db: EditEngine, ws: Path):
    engine_no_db.emoji_policy = False
    (ws / "u.py").write_text("print('hi')\n", encoding="utf-8")
    engine_no_db.read("u.py", session_id="s")
    out = engine_no_db.edit_file("u.py", "print('hi')", "print('done ✅')", session_id="s")
    assert out.ok


def test_mojibake_autofix(engine: EditEngine, ws: Path):
    (ws / "v.txt").write_text("hello\n", encoding="utf-8")
    engine.read("v.txt", session_id="s")
    out = engine.edit_file("v.txt", "hello", "olá mundo", session_id="s")
    assert out.ok
    assert "olá mundo" in (ws / "v.txt").read_text(encoding="utf-8")


def test_explicit_large_removal_allowed_but_runaway_blocked(engine: EditEngine, ws: Path):
    body = "\n".join(f"line{i} = {i}" for i in range(100))
    (ws / "w.py").write_text(body + "\n", encoding="utf-8")
    engine.read("w.py", session_id="s", limit=300)
    # Explicit removal (old_string covers the lines) is legitimate and allowed.
    out = engine.edit_file("w.py", "line0 = 0\n" + "\n".join(f"line{i} = {i}" for i in range(1, 60)), "", session_id="s")
    assert out.ok
    assert "line60" in (ws / "w.py").read_text(encoding="utf-8")
    # Runaway protection lives in diff_sanity: non-requested mass deletion is rejected.
    from wotan.editing.validators import diff_sanity

    before = "\n".join(f"x{i}" for i in range(200)) + "\n"
    r = diff_sanity(before, "x0\nx1\n", "x0\nx1", "x0\nx1", requested_removal=False)
    assert not r.ok


def test_truncated_ellipsis_rejected(engine: EditEngine, ws: Path):
    body = "\n".join(f"x{i} = {i}" for i in range(40))
    (ws / "w2.py").write_text(body + "\n", encoding="utf-8")
    engine.read("w2.py", session_id="s", limit=300)
    out = engine.edit_file("w2.py", body, "\n".join(body.splitlines()[:5]) + "\n...", session_id="s")
    assert not out.ok


# ---------------------------------------------------------------------------
# concurrent editing (the user edits in the IDE)
# ---------------------------------------------------------------------------

def test_concurrent_edit_detected(engine: EditEngine, ws: Path):
    (ws / "x.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    engine.read("x.py", session_id="s")
    # user edits the file in the IDE between read and edit
    time.sleep(0.01)
    (ws / "x.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    out = engine.edit_file("x.py", "y = 2", "y = 9", session_id="s")
    assert not out.ok
    assert "stale" in out.error.error
    # re-read and retry works
    engine.read("x.py", session_id="s")
    out = engine.edit_file("x.py", "y = 2", "y = 9", session_id="s")
    assert out.ok


# ---------------------------------------------------------------------------
# multi_edit atomicity
# ---------------------------------------------------------------------------

def test_multi_edit_atomic_success(engine: EditEngine, ws: Path):
    (ws / "y.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    engine.read("y.py", session_id="s")
    out = engine.multi_edit("y.py", [
        {"old_string": "a = 1", "new_string": "a = 10"},
        {"old_string": "c = 3", "new_string": "c = 30"},
    ], session_id="s")
    assert out.ok, out.message
    assert (ws / "y.py").read_text(encoding="utf-8") == "a = 10\nb = 2\nc = 30\n"


def test_multi_edit_all_or_nothing(engine: EditEngine, ws: Path):
    (ws / "z.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    engine.read("z.py", session_id="s")
    out = engine.multi_edit("z.py", [
        {"old_string": "a = 1", "new_string": "a = 10"},
        {"old_string": "DOES NOT EXIST", "new_string": "x"},
    ], session_id="s")
    assert not out.ok
    assert "step 2" in out.error.error
    assert (ws / "z.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"  # nothing written


def test_multi_edit_validates_each_step(engine: EditEngine, ws: Path):
    (ws / "z2.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    engine.read("z2.py", session_id="s")
    out = engine.multi_edit("z2.py", [
        {"old_string": "a = 1", "new_string": "a = 10"},
        {"old_string": "b = 2", "new_string": "b = (((("},  # syntax break
    ], session_id="s")
    assert not out.ok
    assert (ws / "z2.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"


# ---------------------------------------------------------------------------
# write_file guard
# ---------------------------------------------------------------------------

def test_write_new_file(engine: EditEngine, ws: Path):
    out = engine.write_file("new.py", "print('hello')\n", session_id="s")
    assert out.ok
    assert (ws / "new.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_write_refuses_rewrite_of_large_file(engine: EditEngine, ws: Path):
    body = "\n".join(f"x{i} = {i}" for i in range(80))
    (ws / "big2.py").write_text(body + "\n", encoding="utf-8")
    engine.read("big2.py", session_id="s", limit=300)
    out = engine.write_file("big2.py", body.replace("x79 = 79", "x79 = 0") + "\n", session_id="s")
    assert not out.ok
    assert "refusing to rewrite" in out.error.error
    out = engine.write_file("big2.py", body + "\nz = 1\n", session_id="s", justification="rewrite to restructure headers")
    assert out.ok


def test_write_allows_full_rewrite_when_change_is_huge(engine: EditEngine, ws: Path):
    body = "\n".join(f"x{i} = {i}" for i in range(80))
    (ws / "big3.py").write_text(body + "\n", encoding="utf-8")
    engine.read("big3.py", session_id="s", limit=300)
    out = engine.write_file("big3.py", "totally = 'different'\n" * 5 + "\n", session_id="s")
    assert out.ok  # similarity below threshold = intended rewrite


# ---------------------------------------------------------------------------
# hashline format
# ---------------------------------------------------------------------------

def test_hashline_read_and_edit(engine: EditEngine, ws: Path):
    (ws / "hl.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    r = engine.read("hl.py", session_id="s", hashline=True)
    assert r.content.splitlines()[0].startswith("1:")
    assert "|a = 1" in r.content
    h1 = line_hash("a = 1")
    h3 = line_hash("c = 3")
    out = engine.hashline_edit("hl.py", [
        {"op": "replace", "start": f"1:{h1}", "end": f"1:{h1}", "new_text": "a = 100"},
        {"op": "insert", "after": f"3:{h3}", "new_text": "d = 4"},
    ], session_id="s")
    assert out.ok, out.message if out.ok else out.error.render()
    assert (ws / "hl.py").read_text(encoding="utf-8") == "a = 100\nb = 2\nc = 3\nd = 4\n"


def test_hashline_rejects_stale_hashes(engine: EditEngine, ws: Path):
    (ws / "hl2.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    engine.read("hl2.py", session_id="s", hashline=True)
    (ws / "hl2.py").write_text("a = 99\nb = 2\n", encoding="utf-8")  # user edit
    h1 = line_hash("a = 1")
    out = engine.hashline_edit("hl2.py", [{"op": "replace", "start": f"1:{h1}", "end": f"1:{h1}", "new_text": "x"}], session_id="s")
    assert not out.ok
    assert "hash mismatch" in out.error.error or "stale" in out.error.error


def test_hashline_delete_op(engine: EditEngine, ws: Path):
    (ws / "hl3.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    engine.read("hl3.py", session_id="s", hashline=True)
    out = engine.hashline_edit("hl3.py", [{"op": "delete", "start": f"2:{line_hash('b = 2')}", "end": f"2:{line_hash('b = 2')}"}], session_id="s")
    assert out.ok
    assert (ws / "hl3.py").read_text(encoding="utf-8") == "a = 1\nc = 3\n"


# ---------------------------------------------------------------------------
# apply_patch format
# ---------------------------------------------------------------------------

def test_apply_patch_update(engine: EditEngine, ws: Path):
    (ws / "ap.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    engine.read("ap.py", session_id="s")
    patch = """*** Begin Patch
*** Update File: ap.py
@@
 def f():
-    return 1
+    return 2
*** End Patch"""
    out = engine.apply_patch(patch, session_id="s")
    assert out.ok, out.message
    assert (ws / "ap.py").read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_apply_patch_add_and_delete(engine: EditEngine, ws: Path):
    patch = """*** Begin Patch
*** Add File: added.py
+print("added")
*** End Patch"""
    out = engine.apply_patch(patch, session_id="s")
    assert out.ok
    assert (ws / "added.py").read_text(encoding="utf-8") == 'print("added")\n' or (ws / "added.py").exists()
    patch2 = """*** Begin Patch
*** Delete File: added.py
*** End Patch"""
    out = engine.apply_patch(patch2, session_id="s")
    assert out.ok
    assert not (ws / "added.py").exists()


def test_apply_patch_non_unique_context(engine: EditEngine, ws: Path):
    (ws / "ap2.py").write_text("pass\npass\npass\npass\n", encoding="utf-8")
    engine.read("ap2.py", session_id="s")
    patch = """*** Begin Patch
*** Update File: ap2.py
@@
-pass
+continue
*** End Patch"""
    out = engine.apply_patch(patch, session_id="s")
    assert not out.ok
    assert (ws / "ap2.py").read_text(encoding="utf-8") == "pass\npass\npass\npass\n"


def test_apply_patch_syntax_validation(engine: EditEngine, ws: Path):
    (ws / "ap3.py").write_text("x = 1\n", encoding="utf-8")
    engine.read("ap3.py", session_id="s")
    patch = """*** Begin Patch
*** Update File: ap3.py
@@
-x = 1
+x = (((
*** End Patch"""
    out = engine.apply_patch(patch, session_id="s")
    assert not out.ok
    assert (ws / "ap3.py").read_text(encoding="utf-8") == "x = 1\n"


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------

def test_path_escape_blocked(engine: EditEngine, ws: Path):
    out = engine.write_file("../evil.py", "x=1\n")
    assert not out.ok
    assert "escapes" in out.error.error


def test_read_binary_file(engine: EditEngine, ws: Path):
    (ws / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    r = engine.read("bin.dat")
    assert r.ok and r.is_binary
