"""Execution log: every command the agent runs is recorded with exit code and
output hash. The verification gate cross-checks finish_task evidence against
this log - the harness does not trust what the model claims."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import Database
from ..paths import state_dir
from ..util import new_id, truncate


@dataclass
class RunRecord:
    id: str
    session_id: str
    task_id: str
    kind: str  # bash | python | test | hook | verifier
    command: str
    cwd: str
    started_at: float
    ended_at: float | None = None
    exit_code: int | None = None
    output_file: str = ""
    output_summary: str = ""
    output_hash: str = ""
    verified: bool = False

    @property
    def finished(self) -> bool:
        return self.ended_at is not None


class ExecutionLog:
    def __init__(self, db: Database, offload_threshold: int = 20_000) -> None:
        self.db = db
        self.offload_threshold = offload_threshold
        self._memory: dict[str, RunRecord] = {}

    def start(self, kind: str, command: str, cwd: str = "", session_id: str = "", task_id: str = "") -> str:
        rid = new_id("r-")
        self.db.add_run(
            id=rid,
            session_id=session_id,
            task_id=task_id,
            kind=kind,
            command=command,
            cwd=cwd,
            started_at=time.time(),
        )
        self._memory[rid] = RunRecord(
            id=rid, session_id=session_id, task_id=task_id, kind=kind,
            command=command, cwd=cwd, started_at=time.time(),
        )
        return rid

    def finish(self, run_id: str, exit_code: int, output: str = "") -> RunRecord:
        rec = self._memory.get(run_id)
        if rec is None:
            row = self.db.get_run(run_id)
            if row is None:
                raise KeyError(f"unknown run {run_id}")
            rec = RunRecord(
                id=run_id, session_id=row["session_id"], task_id=row["task_id"], kind=row["kind"],
                command=row["command"], cwd=row["cwd"], started_at=row["started_at"],
            )
            self._memory[run_id] = rec
        rec.ended_at = time.time()
        rec.exit_code = exit_code
        rec.output_hash = hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()
        # Offload large output to a file (model gets a summary + path).
        offloaded = ""
        if len(output) > self.offload_threshold:
            out_dir = state_dir() / "runs"
            out_dir.mkdir(parents=True, exist_ok=True)
            f = out_dir / f"{run_id}.txt"
            f.write_text(output, encoding="utf-8", errors="replace")
            offloaded = str(f)
            rec.output_file = offloaded
        rec.output_summary = truncate(output, 1500)
        self.db.update_run(
            run_id,
            ended_at=rec.ended_at,
            exit_code=exit_code,
            output_file=offloaded,
            output_summary=rec.output_summary,
            output_hash=rec.output_hash,
        )
        return rec

    def get(self, run_id: str) -> RunRecord | None:
        if run_id in self._memory:
            return self._memory[run_id]
        row = self.db.get_run(run_id)
        if not row:
            return None
        return RunRecord(
            id=row["id"], session_id=row["session_id"], task_id=row["task_id"], kind=row["kind"],
            command=row["command"], cwd=row["cwd"], started_at=row["started_at"], ended_at=row["ended_at"],
            exit_code=row["exit_code"], output_file=row["output_file"], output_summary=row["output_summary"],
            output_hash=row["output_hash"], verified=bool(row["verified"]),
        )

    def list(self, session_id: str = "", task_id: str = "") -> list[RunRecord]:
        rows = self.db.list_runs(session_id=session_id, task_id=task_id)
        out = []
        for row in rows:
            rec = self._memory.get(row["id"])
            if rec is None:
                rec = RunRecord(
                    id=row["id"], session_id=row["session_id"], task_id=row["task_id"], kind=row["kind"],
                    command=row["command"], cwd=row["cwd"], started_at=row["started_at"], ended_at=row["ended_at"],
                    exit_code=row["exit_code"], output_file=row["output_file"], output_summary=row["output_summary"],
                    output_hash=row["output_hash"], verified=bool(row["verified"]),
                )
            out.append(rec)
        return out

    def mark_verified(self, run_ids: list[str]) -> None:
        for rid in run_ids:
            self.db.update_run(rid, verified=1)
            if rid in self._memory:
                self._memory[rid].verified = True

    def read_output(self, run_id: str, limit: int = 4000) -> str:
        rec = self.get(run_id)
        if rec is None:
            return ""
        if rec.output_file and Path(rec.output_file).is_file():
            return truncate(Path(rec.output_file).read_text(encoding="utf-8", errors="replace"), limit)
        return rec.output_summary
