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
