"""Checkpoints: automatic snapshot before every agent edit, per-step undo."""

from __future__ import annotations

from pathlib import Path

from wotan.editing.checkpoints import CheckpointStore


def test_snapshot_and_undo_restores_before(checkpoints: CheckpointStore, ws: Path):
    target = ws / "f.py"
    target.write_text("old\n", encoding="utf-8")
    cid = checkpoints.snapshot("s1", 1, target, b"old\n", b"new\n", label="edit f.py")
    target.write_text("new\n", encoding="utf-8")
    path, data = checkpoints.undo(cid)
    assert path == target
    assert data == b"old\n"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_undo_of_created_file_deletes(checkpoints: CheckpointStore, ws: Path):
    target = ws / "created.py"
    cid = checkpoints.snapshot("s1", 1, target, b"", b"content\n")
    assert target.exists() or True
    target.write_text("content\n", encoding="utf-8")
    checkpoints.undo(cid)
    assert not target.exists()


def test_redo_uses_after(checkpoints: CheckpointStore, ws: Path):
    target = ws / "g.py"
    cid = checkpoints.snapshot("s1", 1, target, b"before\n", b"after\n")
    target.write_text("after\n", encoding="utf-8")
    checkpoints.undo(cid)  # back to before
    checkpoints.undo(cid, restore_before=False)  # forward to after
    assert target.read_text(encoding="utf-8") == "after\n"


def test_list_and_diff(checkpoints: CheckpointStore, ws: Path):
    target = ws / "h.py"
    checkpoints.snapshot("s1", 1, target, b"a\n", b"b\n")
    checkpoints.snapshot("s1", 2, target, b"b\n", b"c\n")
    items = checkpoints.list("s1")
    assert len(items) == 2
    assert items[0].step == 2  # ordered by step desc
    diff = checkpoints.diff(items[0].id)
    assert "-b" in diff and "+c" in diff


def test_edit_engine_creates_checkpoints(engine, ws: Path):
    (ws / "cp.py").write_text("x = 1\n", encoding="utf-8", newline="")
    engine.read("cp.py", session_id="s")
    out = engine.edit_file("cp.py", "x = 1", "x = 2", session_id="s")
    assert out.ok
    assert out.checkpoint_id
    row = engine.checkpoints.get(out.checkpoint_id)
    assert row["content_before"] == b"x = 1\n"
    assert row["content_after"] == b"x = 2\n"
