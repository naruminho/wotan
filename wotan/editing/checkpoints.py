"""Checkpoint store: automatic snapshot before every agent edit (per-step undo).

Snapshots live in SQLite (path + before/after blobs). Nothing about secrets:
file content snapshots stay on the local machine in the local database.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import Database


@dataclass
class Checkpoint:
    id: str
    session_id: str
    step: int
    path: str
    encoding: str
    eol: str
    created_at: float
    label: str


class CheckpointStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def snapshot(
        self,
        session_id: str,
        step: int,
        path: str | Path,
        content_before: bytes,
        content_after: bytes,
        encoding: str = "utf-8",
        eol: str = "lf",
        label: str = "",
    ) -> str:
        return self.db.add_checkpoint(
            session_id=session_id,
            step=step,
            path=str(path),
            content_before=content_before,
            content_after=content_after,
            encoding=encoding,
            eol=eol,
            label=label,
        )

    def list(self, session_id: str) -> list[Checkpoint]:
        rows = self.db.list_checkpoints(session_id)
        return [
            Checkpoint(
                id=r["id"],
                session_id=r["session_id"],
                step=r["step"],
                path=r["path"],
                encoding=r["encoding"],
                eol=r["eol"],
                created_at=r["created_at"],
                label=r["label"],
            )
            for r in rows
        ]

    def get(self, checkpoint_id: str) -> dict[str, Any] | None:
        return self.db.get_checkpoint(checkpoint_id)

    def undo(self, checkpoint_id: str, restore_before: bool = True) -> tuple[Path, bytes]:
        """Restore the file to its pre-edit (or post-edit) content."""
        row = self.db.get_checkpoint(checkpoint_id)
        if row is None:
            raise KeyError(f"unknown checkpoint {checkpoint_id!r}")
        target = Path(row["path"])
        data = row["content_before"] if restore_before else row["content_after"]
        if data == b"" and restore_before and row["content_after"]:
            # File was created by this checkpoint's edit: undo = delete.
            if target.exists():
                target.unlink()
            return target, b""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target, data

    def diff(self, checkpoint_id: str) -> str:
        """Unified diff of a checkpoint (for review before accepting)."""
        import difflib

        row = self.db.get_checkpoint(checkpoint_id)
        if row is None:
            return ""
        before = row["content_before"].decode("utf-8", errors="replace").splitlines(keepends=True)
        after = row["content_after"].decode("utf-8", errors="replace").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(before, after, fromfile=f"a/{row['path']}", tofile=f"b/{row['path']}")
        )
