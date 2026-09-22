"""RepoMap.refresh(): directory pruning and symbol/reference extraction.

Regression coverage for two bugs found in production use: (1) the file walk
used Path.rglob("*"), which cannot skip a directory during traversal, so it
fully descended into node_modules/.venv/etc. before filtering - on a real
repo with a committed frontend build this made refresh() take ~17s on every
single agent turn; (2) the reference pass ran one re.search per (file, symbol)
pair - O(files x symbols) - which was the actual dominant cost once the walk
itself was fixed.
"""

from __future__ import annotations

import time
from pathlib import Path

from wotan.agent.repo_map import RepoMap


def test_refresh_prunes_ignored_directories(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def real_symbol():\n    pass\n", encoding="utf-8")

    huge_dir = tmp_path / "node_modules" / "pkg"
    huge_dir.mkdir(parents=True)
    for i in range(50):
        (huge_dir / f"file{i}.js").write_text(f"function noise{i}() {{}}\n", encoding="utf-8")

    rm = RepoMap(root=tmp_path)
    rm.refresh()

    names = {s.name for s in rm.symbols}
    assert "real_symbol" in names
    assert not any(n.startswith("noise") for n in names)
    # the walk itself must not have descended into node_modules at all
    assert not any("node_modules" in s.file for s in rm.symbols)


def test_refresh_tracks_cross_file_references(tmp_path: Path):
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import helper\n\nprint(helper())\n", encoding="utf-8")

    rm = RepoMap(root=tmp_path)
    rm.refresh()

    assert "helper" in rm.refs
    assert "b.py" in rm.refs["helper"]


def test_refresh_is_fast_with_many_ignored_files(tmp_path: Path):
    big = tmp_path / "node_modules"
    big.mkdir()
    for i in range(300):
        (big / f"f{i}.js").write_text("x" * 200, encoding="utf-8")
    (tmp_path / "main.py").write_text("def entry():\n    pass\n", encoding="utf-8")

    rm = RepoMap(root=tmp_path)
    t0 = time.time()
    rm.refresh()
    assert time.time() - t0 < 3.0
