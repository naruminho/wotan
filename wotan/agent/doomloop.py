"""Doom-loop detection: the heuristics measured in production by other tools.

* the same file edited 5+ times in a short window without progress,
* 3+ consecutive tool calls with errors,
* repeated identical calls,
* model output repeating the same reasoning.

Escalating intervention: (1) inject a warning and force re-planning,
(2) escalate the step to a stronger model from the model picker,
(3) stop and ask the user.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

MAX_FILE_EDITS = 5
MAX_CONSECUTIVE_ERRORS = 3
MAX_IDENTICAL_CALLS = 3
MAX_REPEAT_OUTPUT = 3
WINDOW_SECONDS = 300.0


@dataclass
class DoomEvent:
    kind: str
    detail: str
    action: str  # warn | escalate | stop
    message: str


@dataclass
class DoomLoopDetector:
    window_seconds: float = WINDOW_SECONDS
    file_edits: dict[str, deque[float]] = field(default_factory=dict)
    consecutive_errors: int = 0
    last_call_key: str = ""
    identical_call_count: int = 0
    output_hashes: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    level: int = 0  # 0 = fine, 1 = warned, 2 = escalated, 3 = stopped

    def _trim(self, q: deque[float]) -> None:
        cutoff = time.time() - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()

    def note_edit(self, path: str) -> DoomEvent | None:
        q = self.file_edits.setdefault(path, deque())
        q.append(time.time())
        self._trim(q)
        self.consecutive_errors = 0
        if len(q) >= MAX_FILE_EDITS and self.level < 1:
            self.level = 1
            return DoomEvent(
                kind="file_edit_loop",
                detail=f"{path} edited {len(q)} times in {int(self.window_seconds)}s",
                action="warn",
                message=(
                    "WARNING: you have edited the same file many times without visible progress. "
                    "STOP, re-read the file, write down what is wrong in your task notes, and re-plan before editing again."
                ),
            )
        return None

    def note_tool_result(self, ok: bool, call_key: str) -> DoomEvent | None:
        if ok:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1
        if call_key == self.last_call_key:
            self.identical_call_count += 1
        else:
            self.last_call_count_reset()
            self.last_call_key = call_key
            self.identical_call_count = 1
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS and self.level < 1:
            self.level = 1
            return DoomEvent(
                kind="error_streak",
                detail=f"{self.consecutive_errors} consecutive tool errors",
                action="warn",
                message=(
                    "WARNING: the last several tool calls failed. Re-read the file/state, simplify the approach, "
                    "and describe the problem in your task notes before trying again."
                ),
            )
        if self.identical_call_count >= MAX_IDENTICAL_CALLS and self.level < 2:
            self.level = 2
            return DoomEvent(
                kind="identical_calls",
                detail=f"identical call repeated {self.identical_call_count}x",
                action="escalate",
                message=(
                    "ESCALATION: the same tool call is repeating with no progress. "
                    "This step is escalated to the stronger model - simplify the task and continue there."
                ),
            )
        return None

    def last_call_count_reset(self) -> None:
        self.identical_call_count = 0

    def note_assistant_text(self, text: str) -> DoomEvent | None:
        import hashlib

        h = hashlib.sha1(text.strip()[:2000].encode()).hexdigest()
        repeats = 0
        for prev in self.output_hashes:
            if prev == h:
                repeats += 1
        self.output_hashes.append(h)
        if repeats >= MAX_REPEAT_OUTPUT:
            self.level = 3
            return DoomEvent(
                kind="repeating_output",
                detail="assistant output repeats itself",
                action="stop",
                message=(
                    "STOP: the model is looping with repeated output. The harness is halting and asking the user."
                ),
            )
        return None

    def reset(self) -> None:
        self.file_edits.clear()
        self.consecutive_errors = 0
        self.identical_call_count = 0
        self.output_hashes.clear()
        self.level = 0

    def status(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "consecutive_errors": self.consecutive_errors,
            "identical_call_count": self.identical_call_count,
            "file_edits": {p: len(q) for p, q in self.file_edits.items()},
        }
