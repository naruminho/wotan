"""Integrated terminal: PTY on the backend.

Windows 10/11 is the target: ConPTY via ``pywinpty``. POSIX (dev/test only)
uses the stdlib :mod:`pty`. Output is decoded tolerantly as UTF-8 so accented
characters never break the session.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import subprocess_utf8_env
from ..util import new_id

IS_WINDOWS = sys.platform == "win32"


@dataclass
class TerminalSession:
    id: str
    shell: str
    cwd: str
    rows: int = 24
    cols: int = 80
    alive: bool = True
    process: Any = None
    reader_task: asyncio.Task | None = None
    on_output: Any = None  # async callback(bytes)
    _win_pty: Any = field(default=None, repr=False)


class TerminalManager:
    """Manages multiple terminal instances (tabs in the terminal panel)."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.sessions: dict[str, TerminalSession] = {}

    def default_shell(self) -> str:
        if IS_WINDOWS:
            return "powershell.exe"
        return os.environ.get("SHELL", "/bin/bash")

    def list(self) -> list[dict[str, Any]]:
        return [{"id": s.id, "shell": s.shell, "cwd": s.cwd, "alive": s.alive} for s in self.sessions.values()]

    async def create(self, shell: str = "", cwd: str = "", on_output: Any = None) -> TerminalSession:
        sid = new_id("term-")
        shell = shell or self.default_shell()
        workdir = str(self.workspace / cwd) if cwd else str(self.workspace)
        ts = TerminalSession(id=sid, shell=shell, cwd=workdir, on_output=on_output)
        if IS_WINDOWS:
            self._start_windows(ts)
        else:
            await self._start_posix(ts)
        self.sessions[sid] = ts
        return ts

    def _start_windows(self, ts: TerminalSession) -> None:
        """ConPTY via pywinpty (no admin required)."""
        try:
            from winpty import PtyProcess  # type: ignore

            env = subprocess_utf8_env()
            ts.process = PtyProcess.spawn(ts.shell, cwd=ts.cwd, env=env, dimensions=(ts.rows, ts.cols))

            def _reader() -> None:
                try:
                    while ts.process.isalive():
                        data = ts.process.read(4096)
                        if data:
                            data_b = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
                            if ts.on_output:
                                asyncio.run_coroutine_threadsafe(ts.on_output(data_b), asyncio.get_event_loop())
                except Exception:
                    pass
                ts.alive = False

            import threading

            threading.Thread(target=_reader, daemon=True).start()
        except Exception:
            # Fallback: plain pipes (no interactive TTY features)
            self._start_pipe(ts)

    async def _start_posix(self, ts: TerminalSession) -> None:
        try:
            import pty  # noqa: F401

            self._start_pipe(ts, use_pty=True)
        except Exception:
            self._start_pipe(ts)

    def _start_pipe(self, ts: TerminalSession, use_pty: bool = False) -> None:
        import subprocess

        ts.process = subprocess.Popen(
            [ts.shell] if not use_pty else [ts.shell, "-i"],
            cwd=ts.cwd,
            env=subprocess_utf8_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

        def _reader() -> None:
            try:
                assert ts.process.stdout is not None
                while True:
                    data = ts.process.stdout.read(4096)
                    if not data:
                        break
                    if ts.on_output:
                        asyncio.run_coroutine_threadsafe(ts.on_output(data), asyncio.get_event_loop())
            except Exception:
                pass
            ts.alive = False

        import threading

        threading.Thread(target=_reader, daemon=True).start()

    def write(self, session_id: str, data: str | bytes) -> None:
        ts = self.sessions.get(session_id)
        if ts is None or ts.process is None:
            return
        raw = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
        try:
            if IS_WINDOWS and ts._win_pty is None and hasattr(ts.process, "write"):
                ts.process.write(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw)
                return
        except Exception:
            pass
        try:
            if hasattr(ts.process, "stdin") and ts.process.stdin:
                ts.process.stdin.write(raw)
                ts.process.stdin.flush()
        except Exception:
            pass

    def resize(self, session_id: str, rows: int, cols: int) -> None:
        ts = self.sessions.get(session_id)
        if ts is None:
            return
        ts.rows, ts.cols = rows, cols
        try:
            if hasattr(ts.process, "setwinsize"):
                ts.process.setwinsize(rows, cols)
        except Exception:
            pass

    def kill(self, session_id: str) -> None:
        ts = self.sessions.pop(session_id, None)
        if ts is None or ts.process is None:
            return
        ts.alive = False
        try:
            if IS_WINDOWS and hasattr(ts.process, "close"):
                ts.process.close(True)
                return
        except Exception:
            pass
        try:
            ts.process.send_signal(signal.SIGKILL)
        except Exception:
            try:
                ts.process.kill()
            except Exception:
                pass

    def kill_all(self) -> None:
        for sid in list(self.sessions):
            self.kill(sid)
