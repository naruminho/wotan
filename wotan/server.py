"""FastAPI application: serves the bundled frontend and exposes the API.

REST covers filesystem, search, git, settings, logs, checkpoints, memory,
skills, scheduler, experiments. WebSockets stream the agent loop
(``/ws/agent``) and terminals (``/ws/terminal/{id}``). Experiment previews are
proxied under ``/preview/{name}/`` so the browser only ever talks to this
origin (never to raw localhost ports).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agent.session import AgentSession
from .providers.base import Attachment

MAX_ATTACHMENT_B64_CHARS = 8_000_000  # ~6MB raw after base64 decoding
AGENT_TERMINAL_STATUSES = {"idle", "done", "error", "stopped"}
from .config import AppConfig, load_config, save_config
from .db import Database, get_db
from .editing.checkpoints import CheckpointStore
from .logging_setup import diagnostic_bundle_files, get_logger, ring, setup_logging
from .paths import STATIC_DIR, ASSETS_DIR, add_recent_workspace, memory_dir, read_recent_workspaces, subprocess_utf8_env, wotan_home
from .security import scan_secrets
from .terminal import TerminalManager
from .util import new_id, truncate, utc_iso

log = get_logger("wotan.server", component="server")

MAX_IDE_EDIT_LINES = 200_000
PROTECTED_IDE_FILES = {"pyproject.toml", "package.json", "tsconfig.json"}  # still editable in IDE (user action), but flagged


def _workspace(request: Request) -> Path:
    return request.app.state.workspace


def _safe_path(request: Request, rel: str) -> Path:
    ws = _workspace(request).resolve()
    p = (ws / rel).resolve() if rel else ws
    try:
        p.relative_to(ws)
    except ValueError:
        raise PermissionError(f"path escapes the workspace: {rel}")
    return p


GITIGNORE_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".wotan"}


def create_app(workspace: str | Path | None = None, config: AppConfig | None = None, db: Database | None = None) -> FastAPI:
    ws = Path(workspace or os.environ.get("WOTAN_WORKSPACE") or Path.cwd()).resolve()
    setup_logging((config.logging.level if config else os.environ.get("WOTAN_LOG_LEVEL", "INFO")))
    app = FastAPI(title="Wotan", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.workspace = ws
    app.state.config = config or load_config(workspace=ws)
    app.state.db = db or get_db()
    app.state.checkpoints = CheckpointStore(app.state.db)
    app.state.terminals = TerminalManager(ws)
    app.state.agent: AgentSession | None = None
    app.state.experiments: dict[str, dict[str, Any]] = {}
    app.state.started_at = time.time()

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        cfg: AppConfig = app.state.config
        return {
            "ok": True, "app": "Wotan", "version": __version__, "workspace": str(ws), "time": utc_iso(),
            "default_permission_mode": cfg.permissions.default_mode,
        }

    @app.post("/api/workspace")
    async def switch_workspace(request: Request) -> dict[str, Any]:
        """Open a different folder without restarting the server."""
        nonlocal ws
        body = await request.json()
        raw = str(body.get("path", "")).strip()
        if not raw:
            return {"ok": False, "error": "path is required"}
        new_ws = Path(raw).expanduser()
        if not new_ws.is_absolute():
            return {"ok": False, "error": "provide an absolute path"}
        if not new_ws.exists():
            return {"ok": False, "error": f"path does not exist: {new_ws}"}
        if not new_ws.is_dir():
            return {"ok": False, "error": f"not a directory: {new_ws}"}
        new_ws = new_ws.resolve()

        running = app.state.agent
        if running is not None and running.state.status not in AGENT_TERMINAL_STATUSES:
            return {"ok": False, "error": "the agent is still running - stop it before switching folders"}

        ws = new_ws
        app.state.workspace = new_ws
        app.state.config = load_config(workspace=new_ws)
        app.state.terminals = TerminalManager(new_ws)
        app.state.agent = None  # next /ws/agent connection creates a fresh session bound to the new folder
        add_recent_workspace(new_ws)
        log.info("workspace switched", extra={"data": {"path": str(new_ws)}})
        return {"ok": True, "workspace": str(new_ws)}

    @app.get("/api/recent-workspaces")
    async def recent_workspaces() -> dict[str, Any]:
        return {"paths": [str(p) for p in read_recent_workspaces() if p != app.state.workspace]}

    @app.get("/api/browse-dirs")
    async def browse_dirs(path: str = "") -> dict[str, Any]:
        """List subdirectories of an arbitrary filesystem path, for the 'Open folder'
        picker. Deliberately NOT scoped to the current workspace (_safe_path) - its
        whole job is to let the user navigate anywhere on disk to pick a new one."""
        raw = path.strip()
        if not raw:
            if sys.platform.startswith("win"):
                import string

                drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
                return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives]}
            return {"path": "", "parent": None, "dirs": [{"name": "/", "path": "/"}]}

        p = Path(raw).expanduser()
        if not p.is_absolute():
            return {"error": "provide an absolute path"}
        if not p.exists() or not p.is_dir():
            return {"error": f"not a directory: {p}"}
        p = p.resolve()

        entries = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                try:
                    if child.is_dir():
                        entries.append({"name": child.name, "path": str(child)})
                except OSError:
                    continue  # broken symlink, permission denied on stat, etc.
        except PermissionError:
            return {"error": f"permission denied: {p}"}

        parent = str(p.parent) if p.parent != p else None
        # On Windows, going up from a drive root (C:\) should surface the drive list.
        is_drive_root = sys.platform.startswith("win") and len(str(p)) <= 3
        return {"path": str(p), "parent": "" if is_drive_root else parent, "dirs": entries}

    # --------------------------------------------------------------- frontend
    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        cfg: AppConfig = app.state.config
        groups: dict[str, list[dict[str, Any]]] = {}
        for p, m in cfg.all_models():
            groups.setdefault(p.id, []).append({
                "id": m.id, "ref": f"{p.id}/{m.id}", "name": m.name or m.id,
                "provider": p.id, "provider_type": p.type, "context_window": m.context_window,
                "tools": m.tools, "streaming": m.streaming, "weak": m.weak, "multimodal": m.multimodal,
                "edit_format": m.edit_format or ("hashline" if m.weak else "str_replace"),
                "description": m.description,
            })
        return {
            "groups": groups,
            "default": cfg.default_model or "",
            "roles": {
                "planner": cfg.roles.planner, "executor": cfg.roles.executor,
                "summarizer": cfg.roles.summarizer, "subagent": cfg.roles.subagent, "verifier": cfg.roles.verifier,
            },
        }

    # -------------------------------------------------------------------- fs
    def fs_routes(app: FastAPI) -> None:
        @app.get("/api/fs/tree")
        async def _fs_tree(request: Request, path: str = "") -> dict[str, Any]:
            root = _safe_path(request, path)
            if not root.is_dir():
                return {"path": path, "entries": []}
            entries = []
            try:
                for child in sorted(root.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
                    if child.name.startswith("."):
                        if child.name not in (".wotan",):
                            continue
                    if child.is_dir() and child.name in GITIGNORE_SKIP_DIRS:
                        continue
                    entries.append({
                        "name": child.name,
                        "path": str(Path(path) / child.name).replace("\\", "/") if path else child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else 0,
                    })
            except PermissionError:
                return {"path": path, "entries": [], "error": "permission denied"}
            return {"path": path, "entries": entries}

        @app.get("/api/fs/file")
        async def fs_file(request: Request, path: str) -> JSONResponse:
            try:
                p = _safe_path(request, path)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if not p.is_file():
                return JSONResponse({"error": f"not found: {path}"}, status_code=404)
            data = p.read_bytes()
            from .editing.encodings import decode_bytes

            if b"\x00" in data[:8192]:
                return JSONResponse({"path": path, "binary": True, "size": len(data)})
            text, enc, had_bom = decode_bytes(data)
            crlf = text.count("\r\n")
            lf_only = text.count("\n") - crlf
            eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
            read_only = text.count("\n") + 1 > MAX_IDE_EDIT_LINES
            return JSONResponse({
                "path": path, "content": text, "encoding": enc + ("+bom" if had_bom else ""),
                "eol": eol, "read_only": read_only, "lines": text.count("\n") + 1,
                "protected": p.name in PROTECTED_IDE_FILES,
            })

        @app.put("/api/fs/file")
        async def fs_save(request: Request) -> JSONResponse:
            body = await request.json()
            rel = body.get("path", "")
            content = body.get("content", "")
            expected_hash = body.get("expected_hash")
            try:
                p = _safe_path(request, rel)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            from .editing.engine import sha256_text
            from .editing.encodings import decode_bytes, encode_text, new_file_bytes

            existed = p.is_file()
            if existed:
                data = p.read_bytes()
                text, enc, had_bom = decode_bytes(data)
                if expected_hash and sha256_text(text) != expected_hash:
                    return JSONResponse({"error": "conflict: file changed on disk since it was opened", "conflict": True}, status_code=409)
                crlf = text.count("\r\n")
                lf_only = text.count("\n") - crlf
                from .editing.encodings import FileFormat

                fmt = FileFormat(encoding=enc, has_bom=had_bom, eol="mixed" if crlf and lf_only else ("crlf" if crlf else "lf"))
                fmt.crlf_count, fmt.lf_count = crlf, lf_only
                p.write_bytes(encode_text(content, fmt, new_eol="preserve"))
                app.state.checkpoints.snapshot("ide", int(time.time()), rel, data, p.read_bytes(), label="IDE save")
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                eol = app.state.config.editor.new_file_eol
                p.write_bytes(new_file_bytes(content, app.state.config.editor.new_file_encoding, eol))
            from .editing.engine import sha256_text as _sha

            return JSONResponse({"ok": True, "path": rel, "hash": _sha(content)})

        @app.post("/api/fs/create")
        async def fs_create(request: Request) -> JSONResponse:
            body = await request.json()
            try:
                p = _safe_path(request, body.get("path", ""))
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if body.get("type") == "dir":
                p.mkdir(parents=True, exist_ok=True)
            else:
                if p.exists():
                    return JSONResponse({"error": "already exists"}, status_code=409)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("", encoding="utf-8")
            return JSONResponse({"ok": True})

        @app.post("/api/fs/rename")
        async def fs_rename(request: Request) -> JSONResponse:
            body = await request.json()
            try:
                src = _safe_path(request, body.get("path", ""))
                dst = _safe_path(request, body.get("new_path", ""))
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            src.rename(dst)
            return JSONResponse({"ok": True})

        @app.post("/api/fs/delete")
        async def fs_delete(request: Request) -> JSONResponse:
            body = await request.json()
            try:
                p = _safe_path(request, body.get("path", ""))
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            return JSONResponse({"ok": True})

        @app.post("/api/fs/move")
        async def fs_move(request: Request) -> JSONResponse:
            return await fs_rename(request)

        @app.get("/api/search")
        async def search(request: Request, q: str, glob: str = "", limit: int = 100) -> dict[str, Any]:
            root = _workspace(request)
            try:
                rx = re.compile(q, re.IGNORECASE)
            except re.error:
                rx = re.compile(re.escape(q), re.IGNORECASE)
            import fnmatch

            hits = []
            for p in sorted(root.rglob("*")):
                if not p.is_file() or any(part in GITIGNORE_SKIP_DIRS for part in p.parts):
                    continue
                if glob and not fnmatch.fnmatch(p.name, glob):
                    continue
                if p.stat().st_size > 1_000_000:
                    continue
                try:
                    text = p.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    continue
                matches = []
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        matches.append({"line": i, "text": truncate(line, 240)})
                        if len(matches) >= 5:
                            break
                if matches:
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    hits.append({"path": rel, "matches": matches})
                    if len(hits) >= limit:
                        break
            return {"query": q, "files": hits, "count": len(hits)}

        # ------------------------------------------------------------------ git
        def _git(request: Request, *args: str) -> tuple[int, str]:
            root = _workspace(request)
            proc = subprocess.run(
                ["git", *args], cwd=str(root), capture_output=True,
                env=subprocess_utf8_env(), timeout=30,
            )
            out = (proc.stdout or b"").decode("utf-8", errors="replace")
            err = (proc.stderr or b"").decode("utf-8", errors="replace")
            return proc.returncode, out + err

        @app.get("/api/git/status")
        async def git_status(request: Request) -> dict[str, Any]:
            rc, out = _git(request, "status", "--porcelain")
            if rc != 0:
                return {"ok": False, "error": truncate(out, 500), "files": []}
            files = []
            for line in out.splitlines():
                if len(line) > 3:
                    files.append({"status": line[:2].strip(), "path": line[3:].strip().strip('"')})
            rc, branch = _git(request, "rev-parse", "--abbrev-ref", "HEAD")
            return {"ok": True, "branch": branch.strip(), "files": files}

        @app.get("/api/git/diff")
        async def git_diff(request: Request, path: str = "", staged: bool = False) -> dict[str, Any]:
            args = ["diff", "--no-color"]
            if staged:
                args.append("--cached")
            if path:
                args.extend(["--", path])
            rc, out = _git(request, *args)
            return {"ok": rc == 0, "diff": out}

        @app.post("/api/git/stage")
        async def git_stage(request: Request) -> dict[str, Any]:
            body = await request.json()
            paths = body.get("paths") or []
            rc, out = _git(request, "add", "--", *paths) if paths else (1, "no paths")
            return {"ok": rc == 0, "output": truncate(out, 500)}

        @app.post("/api/git/unstage")
        async def git_unstage(request: Request) -> dict[str, Any]:
            body = await request.json()
            paths = body.get("paths") or []
            rc, out = _git(request, "restore", "--staged", "--", *paths)
            return {"ok": rc == 0, "output": truncate(out, 500)}

        @app.post("/api/git/commit")
        async def git_commit(request: Request) -> dict[str, Any]:
            body = await request.json()
            message = body.get("message", "")
            secrets = scan_secrets(message)
            if secrets:
                return {"ok": False, "output": f"commit message contains possible secret ({secrets[0].kind}) - refused"}
            rc, out = _git(request, "commit", "-m", message)
            return {"ok": rc == 0, "output": truncate(out, 1000)}

    fs_routes(app)

    # -------------------------------------------------------------- sessions
    @app.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        return {"sessions": app.state.db.list_sessions()}

    @app.get("/api/sessions/{sid}/messages")
    async def session_messages(sid: str) -> dict[str, Any]:
        return {"messages": app.state.db.list_messages(sid), "todos": app.state.db.get_todos(sid)}

    @app.delete("/api/sessions/{sid}")
    async def session_delete(sid: str) -> dict[str, Any]:
        app.state.db.delete_session(sid)
        return {"ok": True}

    # ------------------------------------------------------------- settings
    @app.get("/api/settings")
    async def settings_get() -> dict[str, Any]:
        from .paths import config_file

        cf = config_file()
        raw = cf.read_text(encoding="utf-8") if cf.is_file() else ""
        cfg: AppConfig = app.state.config
        return {
            "yaml": raw,
            "path": str(cf),
            "summary": {
                "providers": [{"id": p.id, "type": p.type, "models": [m.id for m in p.models]} for p in cfg.providers],
                "default_model": cfg.default_model,
                "permission_mode": cfg.permissions.default_mode,
                "search_enabled": cfg.search.enabled,
            },
        }

    @app.post("/api/settings")
    async def settings_save(request: Request) -> dict[str, Any]:
        body = await request.json()
        yaml_text = body.get("yaml", "")
        import yaml as _yaml

        from .config import parse_config
        from .paths import config_file

        try:
            data = _yaml.safe_load(yaml_text) or {}
            cfg = parse_config(data, source="ui")
        except Exception as exc:
            return {"ok": False, "error": f"invalid configuration: {exc}"}
        cf = config_file()
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(yaml_text, encoding="utf-8")
        app.state.config = cfg
        return {"ok": True, "path": str(cf)}

    @app.post("/api/settings/test-connection")
    async def settings_test(request: Request) -> dict[str, Any]:
        body = await request.json()
        provider_id = body.get("provider_id", "")
        from .doctor import doctor_provider

        cfg: AppConfig = app.state.config
        try:
            report = await doctor_provider(cfg, provider_id or (cfg.providers[0].id if cfg.providers else ""))
            return {"ok": True, "report": report.checks, "rendered": report.render()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ logs
    @app.get("/api/logs")
    async def logs(level: str = "", q: str = "", limit: int = 500) -> dict[str, Any]:
        return {"entries": ring().snapshot(limit=limit, level=level or None, query=q or None)}

    @app.post("/api/feedback")
    async def frontend_feedback(request: Request) -> dict[str, Any]:
        body = await request.json()
        log.error("frontend error", extra={"data": {"kind": body.get("kind"), "message": truncate(str(body.get("message", "")), 500),
                                                    "stack": truncate(str(body.get("stack", "")), 1000)}})
        return {"ok": True}

    @app.get("/api/diagnostics")
    async def diagnostics() -> StreamingResponse:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in diagnostic_bundle_files():
                with contextlib.suppress(Exception):
                    zf.writestr(f"logs/{f.name}", f.read_text(encoding="utf-8", errors="replace"))
            cfg: AppConfig = app.state.config
            from .util import dumps, mask_mapping

            safe_cfg = mask_mapping(dumps(cfg) if False else {
                "providers": [{"id": p.id, "type": p.type, "base_url": p.base_url} for p in cfg.providers],
                "search": {"enabled": cfg.search.enabled, "provider": cfg.search.provider},
                "version": __version__,
                "platform": sys.platform,
            })
            zf.writestr("config.json", json.dumps(safe_cfg, indent=2))
            zf.writestr("versions.json", json.dumps({"wotan": __version__, "python": sys.version}, indent=2))
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename=wotan-diagnostics-{int(time.time())}.zip"
        })

    # ----------------------------------------------------- checkpoints/todos
    @app.get("/api/checkpoints")
    async def checkpoints(session_id: str = "") -> dict[str, Any]:
        cps = app.state.checkpoints.list(session_id or "ide")
        return {"checkpoints": [vars(c) for c in cps]}

    @app.get("/api/checkpoints/{cid}/diff")
    async def checkpoint_diff(cid: str) -> dict[str, Any]:
        return {"diff": app.state.checkpoints.diff(cid)}

    @app.post("/api/checkpoints/{cid}/undo")
    async def checkpoint_undo(cid: str) -> dict[str, Any]:
        try:
            path, data = app.state.checkpoints.undo(cid, restore_before=True)
            return {"ok": True, "path": str(path), "restored_bytes": len(data)}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @app.get("/api/todos")
    async def todos(session_id: str = "") -> dict[str, Any]:
        return {"todos": app.state.db.get_todos(session_id)}

    # ------------------------------------------------ memory / skills / inbox
    @app.get("/api/memory")
    async def memory_list() -> dict[str, Any]:
        from .agent.memory import MemoryStore

        store = MemoryStore()
        return {"files": [{"name": m.name, "summary": m.summary, "content": m.content} for m in store.list()]}

    @app.post("/api/memory")
    async def memory_save(request: Request) -> dict[str, Any]:
        from .agent.memory import MemoryStore

        body = await request.json()
        MemoryStore().write(body.get("name", "memory.md"), body.get("content", ""))
        return {"ok": True}

    @app.get("/api/skills")
    async def skills(request: Request) -> dict[str, Any]:
        from .agent.skills import SkillRegistry

        reg = SkillRegistry(_workspace(request))
        return {"skills": [{"name": s.name, "description": s.description, "path": str(s.path),
                            "scripts": s.scripts, "references": s.references} for s in reg.scan()]}

    @app.get("/api/inbox")
    async def inbox() -> dict[str, Any]:
        return {"items": app.state.db.list_inbox()}

    @app.post("/api/inbox/read")
    async def inbox_read(request: Request) -> dict[str, Any]:
        body = await request.json()
        app.state.db.mark_inbox_read(body.get("id", ""))
        return {"ok": True}

    # ------------------------------------------------------------- scheduler
    @app.get("/api/scheduler")
    async def scheduler_list() -> dict[str, Any]:
        sch = getattr(app.state, "scheduler", None)
        return {"tasks": sch.list() if sch else []}

    @app.post("/api/scheduler")
    async def scheduler_add(request: Request) -> dict[str, Any]:
        body = await request.json()
        sch = getattr(app.state, "scheduler", None)
        if sch is None:
            from .agent.scheduler import Scheduler

            sch = Scheduler(app.state.db)
            app.state.scheduler = sch
        task = sch.add(body.get("name", ""), body.get("prompt", ""), body.get("when", "in 1 minutes"))
        return {"ok": True, "task": {"id": task.id, "name": task.name, "when": task.cron}}

    @app.delete("/api/scheduler/{tid}")
    async def scheduler_cancel(tid: str) -> dict[str, Any]:
        sch = getattr(app.state, "scheduler", None)
        return {"ok": bool(sch and sch.cancel(tid))}

    # ------------------------------------------------------------ experiments
    @app.get("/api/experiments")
    async def experiments(request: Request) -> dict[str, Any]:
        root = _workspace(request) / "experiments"
        items = []
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir() and (d / "README.md").is_file():
                    run = app.state.experiments.get(d.name, {})
                    items.append({
                        "name": d.name,
                        "running": bool(run.get("proc")),
                        "port": run.get("port"),
                        "pid": run.get("proc").pid if run.get("proc") else None,
                        "preview": f"/preview/{d.name}/" if run.get("port") else "",
                    })
        return {"experiments": items}

    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    @app.post("/api/experiments/{name}/run")
    async def experiment_run(name: str, request: Request) -> dict[str, Any]:
        root = _workspace(request) / "experiments" / name
        if not root.is_dir():
            return {"ok": False, "error": f"unknown experiment {name!r}"}
        existing = app.state.experiments.get(name)
        if existing and existing.get("proc"):
            return {"ok": True, "preview": f"/preview/{name}/", "port": existing["port"], "already_running": True}
        port = _free_port()
        run_py = root / "run.py"
        web_py = root / "webapp.py"
        entry = web_py if web_py.is_file() else run_py
        if not entry.is_file():
            return {"ok": False, "error": "experiment has no run.py/webapp.py"}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(entry), "--port", str(port),
            cwd=str(root), env=subprocess_utf8_env(),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        app.state.experiments[name] = {"proc": proc, "port": port, "started": time.time()}
        return {"ok": True, "preview": f"/preview/{name}/", "port": port, "pid": proc.pid}

    @app.post("/api/experiments/{name}/stop")
    async def experiment_stop(name: str) -> dict[str, Any]:
        run = app.state.experiments.pop(name, None)
        if run and run.get("proc"):
            run["proc"].kill()
        return {"ok": True}

    @app.api_route("/preview/{name}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def preview_proxy(name: str, path: str, request: Request):
        import httpx

        run = app.state.experiments.get(name)
        if not run or not run.get("port"):
            return JSONResponse({"error": f"experiment {name!r} is not running"}, status_code=404)
        port = run["port"]
        url = f"http://127.0.0.1:{port}/{path}"
        if request.url.query:
            url += "?" + request.url.query
        body = await request.body()
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.request(request.method, url, content=body,
                                            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")})
            except httpx.HTTPError as exc:
                return JSONResponse({"error": f"preview unreachable: {exc}"}, status_code=502)
        return StreamingResponse(
            iter([resp.content]),
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )

    @app.get("/preview/{name}/", include_in_schema=False)
    async def preview_root(name: str):
        return RedirectResponse(f"/preview/{name}/index.html")

    # ------------------------------------------------------------- websockets
    @app.websocket("/ws/terminal/{tid}")
    async def ws_terminal(ws: WebSocket, tid: str) -> None:
        await ws.accept()
        tm: TerminalManager = app.state.terminals
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        async def on_output(data: bytes) -> None:
            await queue.put(data)

        session = tm.sessions.get(tid)
        if session is None:
            session = await tm.create(on_output=on_output)
            await ws.send_text(json.dumps({"type": "ready", "id": session.id, "shell": session.shell}))
        else:
            session.on_output = on_output

        async def pump() -> None:
            while True:
                data = await queue.get()
                await ws.send_text(json.dumps({"type": "output", "data": data.decode("utf-8", errors="replace")}))

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                msg = await ws.receive_text()
                m = json.loads(msg)
                if m.get("type") == "input":
                    tm.write(session.id, m.get("data", ""))
                elif m.get("type") == "resize":
                    tm.resize(session.id, int(m.get("rows", 24)), int(m.get("cols", 80)))
                elif m.get("type") == "kill":
                    tm.kill(session.id)
                    break
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()

    @app.websocket("/ws/agent")
    async def ws_agent(ws: WebSocket) -> None:
        await ws.accept()
        db: Database = app.state.db
        send_lock = asyncio.Lock()

        async def emit(event: dict[str, Any]) -> None:
            async with send_lock:
                with contextlib.suppress(Exception):
                    await ws.send_text(json.dumps(event, ensure_ascii=False, default=str))

        existing: AgentSession | None = app.state.agent
        if existing is not None and existing.state.status not in AGENT_TERMINAL_STATUSES:
            # A run is still going from a connection that dropped (network hiccup,
            # laptop woke back up, tab reloaded) - reattach to it instead of
            # starting a fresh session and losing the in-progress turn.
            agent = existing
            agent.emit = emit
            await emit({
                "type": "ready", "session_id": agent.session_id, "version": __version__,
                "resumed": True, "status": agent.state.status,
            })
        else:
            agent = AgentSession(
                config=app.state.config,
                workspace=app.state.workspace,
                db=db,
                emit=emit,
                registry=app.state.agent.registry if app.state.agent else None,
            )
            app.state.agent = agent
            await emit({"type": "ready", "session_id": agent.session_id, "version": __version__})

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(15)
                await emit({"type": "heartbeat", "last_event_age": round(time.time() - agent.state.last_event_at, 1)})

        hb = asyncio.create_task(heartbeat())
        try:
            while True:
                msg = await ws.receive_text()
                m = json.loads(msg)
                if m.get("type") == "run":
                    attachments: list[Attachment] = []
                    for a in m.get("attachments") or []:
                        data_b64 = str(a.get("data_b64", ""))
                        if len(data_b64) > MAX_ATTACHMENT_B64_CHARS:
                            await emit({"type": "error", "message": f"attachment {a.get('name', '')!r} too large - skipped (max ~6MB)"})
                            continue
                        attachments.append(Attachment(
                            kind=str(a.get("kind", "image")),
                            mime=str(a.get("mime", "image/png")),
                            data_b64=data_b64,
                            name=str(a.get("name", "")),
                        ))
                    asyncio.create_task(agent.run_turn(
                        m.get("text", ""),
                        model_ref=m.get("model", ""),
                        agent_mode=m.get("mode"),
                        permission_mode=m.get("permission_mode"),
                        attachments=attachments,
                    ))
                elif m.get("type") in ("reply", "approval_reply"):
                    await agent.resolve_prompt_reply(str(m.get("id", "")), str(m.get("value", "")))
                elif m.get("type") == "stop":
                    await agent.stop()
                elif m.get("type") == "get_status":
                    await emit({"type": "status", "status": agent.state.status, "gate": agent.gate.status(),
                                "usage": {"input_tokens": agent.state.input_tokens,
                                          "output_tokens": agent.state.output_tokens, "steps": agent.state.steps}})
        except WebSocketDisconnect:
            # Keep the agent (and any in-progress turn) alive - a reconnect
            # within a reasonable window reattaches above instead of losing
            # the run. Only an explicit 'stop' message or a completed turn
            # actually ends it.
            pass
        finally:
            hb.cancel()

    # ---------------------------------------------------- gateway proxy (browser -> configured gateway)
    from . import gateway_client as gwsdk
    from .llm_errors import LLMError

    @app.post("/gw/chat")
    async def gw_chat(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            gwsdk.init(workspace=str(ws))
            try:
                answer = await gwsdk.chat(
                    str(body.get("prompt", "")), model=str(body.get("model", "")), system=str(body.get("system", "") or "")
                )
                return JSONResponse({"answer": answer})
            finally:
                await gwsdk.aclose()
        except LLMError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.post("/gw/chat_image")
    async def gw_chat_image(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            import base64
            import tempfile

            data = base64.b64decode(str(body.get("image_b64", "")))
            with tempfile.TemporaryDirectory(prefix="wotan-gw-") as td:
                img = Path(td) / "image.png"
                img.write_bytes(data)
                gwsdk.init(workspace=str(ws))
                try:
                    answer = await gwsdk.chat_with_image(
                        str(body.get("prompt", "")), img, model=str(body.get("model", ""))
                    )
                finally:
                    await gwsdk.aclose()
            return JSONResponse({"answer": answer})
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.post("/gw/chat_document")
    async def gw_chat_document(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            import base64
            import tempfile

            data = base64.b64decode(str(body.get("document_b64", "")))
            with tempfile.TemporaryDirectory(prefix="wotan-gw-") as td:
                doc = Path(td) / str(body.get("filename") or "document.pdf")
                doc.write_bytes(data)
                gwsdk.init(workspace=str(ws))
                try:
                    answer = await gwsdk.chat_with_document(
                        str(body.get("prompt", "")), doc, model=str(body.get("model", ""))
                    )
                finally:
                    await gwsdk.aclose()
            return JSONResponse({"answer": answer})
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.post("/gw/extract_json")
    async def gw_extract_json(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            gwsdk.init(workspace=str(ws))
            try:
                data = await gwsdk.extract_json(
                    str(body.get("prompt", "")), schema=body.get("schema"), model=str(body.get("model", ""))
                )
                return JSONResponse({"data": data})
            finally:
                await gwsdk.aclose()
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.post("/gw/workflow")
    async def gw_workflow(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            gwsdk.init(workspace=str(ws))
            try:
                out = await gwsdk.run_workflow(str(body.get("name", "")), body.get("inputs") or {})
                return JSONResponse({"output": out})
            finally:
                await gwsdk.aclose()
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    @app.get("/gw/models")
    async def gw_models(model_ref: str = "") -> JSONResponse:
        try:
            gwsdk.init(workspace=str(ws))
            try:
                return JSONResponse({"models": await gwsdk.list_models_remote(model_ref)})
            finally:
                await gwsdk.aclose()
        except Exception as exc:
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)

    # -------------------------------------------------------------- static UI
    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets") if (STATIC_DIR / "assets").is_dir() else None

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> FileResponse:
            candidate = STATIC_DIR / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")
    else:

        @app.get("/")
        async def no_ui() -> PlainTextResponse:
            return PlainTextResponse(
                "Wotan backend is running but the frontend is not built.\n"
                "Run: python scripts/build_frontend.py\n"
                "API docs: /api/docs"
            )

    if (ASSETS_DIR / "logo.svg").is_file():
        @app.get("/logo.svg")
        async def logo() -> FileResponse:
            return FileResponse(ASSETS_DIR / "logo.svg")

    return app
