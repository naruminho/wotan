"""Recent-workspaces persistence (~/.wotan/recent_workspaces.json)."""

from __future__ import annotations

from pathlib import Path

from wotan import paths


def test_recent_workspaces_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()

    assert paths.read_recent_workspaces() == []
    assert paths.read_last_workspace() is None

    paths.add_recent_workspace(a)
    paths.add_recent_workspace(b)
    recent = paths.read_recent_workspaces()
    assert [p.name for p in recent] == ["proj_b", "proj_a"]
    assert paths.read_last_workspace() == b.resolve()


def test_recent_workspaces_reopen_moves_to_front(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()

    paths.add_recent_workspace(a)
    paths.add_recent_workspace(b)
    paths.add_recent_workspace(a)  # reopening a must move it back to the front, not duplicate it
    recent = paths.read_recent_workspaces()
    assert [p.name for p in recent] == ["proj_a", "proj_b"]


def test_recent_workspaces_drops_deleted_folders(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    gone = tmp_path / "will_be_deleted"
    gone.mkdir()
    paths.add_recent_workspace(gone)
    gone.rmdir()
    assert paths.read_recent_workspaces() == []


def test_recent_workspaces_capped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    for i in range(paths.MAX_RECENT_WORKSPACES + 5):
        d = tmp_path / f"p{i}"
        d.mkdir()
        paths.add_recent_workspace(d)
    assert len(paths.read_recent_workspaces()) == paths.MAX_RECENT_WORKSPACES
