"""Persistent storage: SQLite for sessions, history, settings, checkpoints,
runs (execution log for the verification gate), inbox and FTS5 session search."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .paths import db_path
from .util import new_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    workspace TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    path TEXT NOT NULL,
    content_before BLOB NOT NULL,
    content_after BLOB NOT NULL,
    encoding TEXT NOT NULL DEFAULT 'utf-8',
    eol TEXT NOT NULL DEFAULT 'lf',
    created_at REAL NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, step);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    command TEXT NOT NULL,
    cwd TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ended_at REAL,
    exit_code INTEGER,
    output_file TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    output_hash TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, started_at);
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    position INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
    ref UNINDEXED, kind, content, tokenize='unicode61'
);
"""


class Database:
    """Tiny thread-safe SQLite wrapper (WAL, check_same_thread off + lock)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # -- generic helpers ----------------------------------------------------
    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sessions & messages -------------------------------------------------
    def create_session(self, title: str = "", workspace: str = "", meta: dict | None = None) -> str:
        sid = new_id("s-")
        t = time.time()
        self.execute(
            "INSERT INTO sessions (id, title, workspace, created_at, updated_at, meta) VALUES (?,?,?,?,?,?)",
            (sid, title, workspace, t, t, json.dumps(meta or {})),
        )
        return sid

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM sessions WHERE id=?", (session_id,))
        return rows[0] if rows else None

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        if title is not None:
            self.execute("UPDATE sessions SET updated_at=?, title=? WHERE id=?", (time.time(), title, session_id))
        else:
            self.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))

    def add_message(self, session_id: str, role: str, content: str, meta: dict | None = None) -> str:
        mid = new_id("m-")
        self.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, meta) VALUES (?,?,?,?,?,?)",
            (mid, session_id, role, content, time.time(), json.dumps(meta or {})),
        )
        self.touch_session(session_id)
        self.index_text(mid, "message", f"{role}: {content}")
        return mid

    def list_messages(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC LIMIT ?", (session_id, limit)
        )
        for r in rows:
            try:
                r["meta"] = json.loads(r.get("meta") or "{}")
            except Exception:
                r["meta"] = {}
        return rows

    def delete_session(self, session_id: str) -> None:
        for table in ("messages", "todos", "checkpoints", "notes"):
            self.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
        self.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            return rows[0]["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # -- checkpoints ---------------------------------------------------------
    def add_checkpoint(
        self,
        session_id: str,
        step: int,
        path: str,
        content_before: bytes,
        content_after: bytes,
        encoding: str = "utf-8",
        eol: str = "lf",
        label: str = "",
    ) -> str:
        cid = new_id("cp-")
        self.execute(
            "INSERT INTO checkpoints (id, session_id, step, path, content_before, content_after, encoding, eol, created_at, label)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, session_id, step, path, content_before, content_after, encoding, eol, time.time(), label),
        )
        return cid

    def list_checkpoints(self, session_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT id, session_id, step, path, encoding, eol, created_at, label FROM checkpoints"
            " WHERE session_id=? ORDER BY step DESC, created_at DESC",
            (session_id,),
        )

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,))
        return rows[0] if rows else None

    # -- runs (execution log, checked by the verification gate) --------------
    def add_run(self, **kw: Any) -> str:
        rid = kw.get("id") or new_id("r-")
        self.execute(
            "INSERT INTO runs (id, session_id, task_id, kind, command, cwd, started_at, ended_at, exit_code,"
            " output_file, output_summary, output_hash, verified)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                kw.get("session_id", ""),
                kw.get("task_id", ""),
                kw.get("kind", "bash"),
                kw.get("command", ""),
                kw.get("cwd", ""),
                kw.get("started_at", time.time()),
                kw.get("ended_at"),
                kw.get("exit_code"),
                kw.get("output_file", ""),
                kw.get("output_summary", ""),
                kw.get("output_hash", ""),
                kw.get("verified", 0),
            ),
        )
        return rid

    def update_run(self, run_id: str, **kw: Any) -> None:
        fields = []
        vals: list[Any] = []
        for k in ("ended_at", "exit_code", "output_file", "output_summary", "output_hash", "verified"):
            if k in kw:
                fields.append(f"{k}=?")
                vals.append(kw[k])
        if not fields:
            return
        vals.append(run_id)
        self.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", tuple(vals))

    def list_runs(self, session_id: str = "", task_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if task_id:
            return self.query(
                "SELECT * FROM runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?", (task_id, limit)
            )
        return self.query("SELECT * FROM runs WHERE session_id=? ORDER BY started_at DESC LIMIT ?", (session_id, limit))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    # -- todos ---------------------------------------------------------------
    def set_todos(self, session_id: str, todos: list[dict[str, Any]]) -> None:
        self.execute("DELETE FROM todos WHERE session_id=?", (session_id,))
        for i, t in enumerate(todos):
            self.execute(
                "INSERT INTO todos (id, session_id, text, status, position, updated_at) VALUES (?,?,?,?,?,?)",
                (t.get("id") or new_id("t-"), session_id, t.get("text", ""), t.get("status", "pending"), i, time.time()),
            )

    def get_todos(self, session_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM todos WHERE session_id=? ORDER BY position ASC", (session_id,))

    # -- inbox ---------------------------------------------------------------
    def add_inbox(self, kind: str, title: str, body: str = "") -> str:
        iid = new_id("in-")
        self.execute(
            "INSERT INTO inbox (id, kind, title, body, created_at, read) VALUES (?,?,?,?,?,0)",
            (iid, kind, title, body, time.time()),
        )
        return iid

    def list_inbox(self, unread_only: bool = False) -> list[dict[str, Any]]:
        if unread_only:
            return self.query("SELECT * FROM inbox WHERE read=0 ORDER BY created_at DESC")
        return self.query("SELECT * FROM inbox ORDER BY created_at DESC LIMIT 200")

    def mark_inbox_read(self, inbox_id: str) -> None:
        self.execute("UPDATE inbox SET read=1 WHERE id=?", (inbox_id,))

    # -- notes & FTS search ---------------------------------------------------
    def save_notes(self, session_id: str, content: str) -> None:
        self.execute(
            "INSERT INTO notes (session_id, content, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
            (session_id, content, time.time()),
        )

    def get_notes(self, session_id: str) -> str:
        rows = self.query("SELECT content FROM notes WHERE session_id=?", (session_id,))
        return rows[0]["content"] if rows else ""

    def index_text(self, ref: str, kind: str, content: str) -> None:
        try:
            with self._lock:
                self._conn.execute("INSERT INTO session_fts (ref, kind, content) VALUES (?,?,?)", (ref, kind, content))
                self._conn.commit()
        except Exception:
            pass  # FTS is best-effort

    def search_sessions(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            return self.query(
                "SELECT ref, kind, snippet(session_fts, 2, '[', ']', '...', 12) AS snip FROM session_fts"
                " WHERE session_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
        except Exception:
            return []


_db: Database | None = None


def get_db(path: Path | None = None) -> Database:
    global _db
    if _db is None or (path is not None and _db.path != path):
        _db = Database(path)
    return _db


def reset_db_singleton() -> None:
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
    _db = None
