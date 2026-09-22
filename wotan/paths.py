"""Filesystem locations used by Wotan.

All paths go through :mod:`pathlib`; Windows is the primary target, so we never
assume POSIX-only semantics.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
ASSETS_DIR = PACKAGE_DIR / "assets"
TEMPLATES_DIR = PACKAGE_DIR / "templates"


def wotan_home() -> Path:
    """User-level state directory: ``%USERPROFILE%\\.wotan`` / ``~/.wotan``.

    Override with the ``WOTAN_HOME`` environment variable (used by tests).
    """
    env = os.environ.get("WOTAN_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".wotan"


def config_file() -> Path:
    """Primary configuration file (YAML)."""
    env = os.environ.get("WOTAN_CONFIG")
    if env:
        return Path(env).expanduser()
    return wotan_home() / "config.yaml"


def config_example_file() -> Path | None:
    """Locate the shipped example configuration (repository layout or package)."""
    candidates = [
        PACKAGE_DIR.parent / "config.example.yaml",
        PACKAGE_DIR / "templates" / "config.example.yaml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def memory_dir() -> Path:
    return wotan_home() / "memory"


def skills_dirs(workspace: Path | None = None) -> list[Path]:
    """Skill folders searched in order (user-level first, then workspace)."""
    dirs = [wotan_home() / "skills"]
    if workspace is not None:
        dirs.append(workspace / ".wotan" / "skills")
        dirs.append(workspace / "skills")
    return dirs


def logs_dir() -> Path:
    return wotan_home() / "logs"


def db_path() -> Path:
    env = os.environ.get("WOTAN_DB")
    if env:
        return Path(env).expanduser()
    return wotan_home() / "wotan.sqlite3"


def state_dir() -> Path:
    """Scratch state: checkpoints blobs, offloaded tool output, runs."""
    return wotan_home() / "state"


MAX_RECENT_WORKSPACES = 10


def recent_workspaces_file() -> Path:
    return wotan_home() / "recent_workspaces.json"


def read_recent_workspaces() -> list[Path]:
    """Most-recently-opened folders (bare `wotan`, or 'Open folder' in the UI),
    most recent first. Entries that no longer exist on disk are dropped silently."""
    import json

    f = recent_workspaces_file()
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[Path] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        p = Path(entry)
        if p.is_dir():
            out.append(p)
    return out


def read_last_workspace() -> Path | None:
    recent = read_recent_workspaces()
    return recent[0] if recent else None


def add_recent_workspace(path: Path) -> None:
    """Push `path` to the front of the recent-folders list (deduplicated, capped)."""
    import json

    resolved = str(Path(path).resolve())
    existing = [str(p) for p in read_recent_workspaces()]
    updated = [resolved] + [p for p in existing if p != resolved]
    updated = updated[:MAX_RECENT_WORKSPACES]
    f = recent_workspaces_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(updated), encoding="utf-8")
    except OSError:
        pass  # best-effort - never let this break opening/switching a folder


def ensure_layout() -> None:
    """Create the user-level directory layout (idempotent)."""
    for d in (wotan_home(), memory_dir(), wotan_home() / "skills", logs_dir(), state_dir()):
        d.mkdir(parents=True, exist_ok=True)


def utf8_console() -> None:
    """Force UTF-8 stdio on Windows consoles (cp1252/cp850 default otherwise)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def subprocess_utf8_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes: PYTHONUTF8=1 and friends."""
    env = dict(base if base is not None else os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
