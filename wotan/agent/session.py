"""Agent session: the tool-calling loop.

model decides -> calls a tool -> receives the result -> continues, until done or
until a step/token limit. Explicit explore -> plan -> execute -> verify -> fix.
Real-time events stream to the UI (tokens, tool cards, approvals, status);
stop works at any moment. The finish_task gate decides completion - the model
cannot declare itself done without evidence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import AppConfig
from ..db import Database
from ..editing.checkpoints import CheckpointStore
from ..editing.engine import EditEngine
from ..logging_setup import get_logger, set_correlation
from ..paths import subprocess_utf8_env
from ..providers.base import Attachment, ChatResult, Message, ToolSpec
from ..providers.errors import LLMError
from ..providers.registry import ProviderRegistry
from ..providers.text_tools import extract_reply_text, parse_tool_calls, validate_arguments
from ..tools import CORE_TOOL_NAMES, ExecResult, ToolContext, ToolRegistry, build_registry
from ..util import new_id, truncate
from .doomloop import DoomLoopDetector
from .execution_log import ExecutionLog
from .hooks import HookRunner, parse_agents_md
from .memory import MemoryStore
from .permissions import PermissionPolicy
from .prompts import INJECTION_NOTE, build_system_prompt, edit_format_for_model
from .repo_map import RepoMap
from .verification import VerificationGate

log = get_logger("wotan.agent.session", component="agent")


# ---------------------------------------------------------------------------
# Persistent shell (ConPTY-backed on Windows via the terminal module; here a
# stdin-driven shell used by tools so state (cd, env) persists across calls).
# ---------------------------------------------------------------------------

class PersistentShell:
    def __init__(self, cwd: Path, windows: bool = False) -> None:
        self.cwd = Path(cwd)
        self.windows = windows
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._proc is not None:
            return
        if self.windows:
            cmd = ["powershell", "-NoLogo", "-NoProfile", "-Command", "-"]
        else:
            cmd = ["/bin/bash", "--noprofile", "--norc"]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.cwd),
            env=subprocess_utf8_env(),
        )

    async def run(self, command: str, timeout: float = 120.0) -> ExecResult:
        async with self._lock:
            await self.start()
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            marker = f"__WOTAN_DONE_{new_id('')}__"
            if self.windows:
                wrapped = f"{command}\nWrite-Output \"{marker}$LASTEXITCODE\""
            else:
                wrapped = f"{command}\nprintf '\\n{marker}%s\\n' \"$?\""
            t0 = time.time()
            self._proc.stdin.write((wrapped + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
            chunks: list[str] = []
            rc: int | None = None
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    line_b = await asyncio.wait_for(self._proc.stdout.readline(), timeout=max(0.1, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                if not line_b:
                    break
                line = line_b.decode("utf-8", errors="replace")
                if marker in line:
                    try:
                        rc = int(line.split(marker, 1)[1].strip() or 0)
                    except ValueError:
                        rc = 0
                    break
                chunks.append(line)
            out = "".join(chunks)
            if rc is None:
                # timed out: restart the shell to get a clean state
                await self.stop()
                return ExecResult(exit_code=124, stdout=out, stderr="command timed out", timed_out=True, duration_s=time.time() - t0)
            return ExecResult(exit_code=rc, stdout=out, duration_s=time.time() - t0)

    async def stop(self) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            self._proc = None


# ---------------------------------------------------------------------------
# Agent session
# ---------------------------------------------------------------------------

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class SessionState:
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    started_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    status: str = "idle"
    current_tool: str = ""
    interrupted: bool = False
    finished: bool = False


class AgentSession:
    def __init__(
        self,
        config: AppConfig,
        workspace: Path,
        db: Database,
        emit: EmitFn,
        session_id: str = "",
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.config = config
        self.workspace = Path(workspace).resolve()
        self.db = db
        self.emit = emit
        self.session_id = session_id or db.create_session(workspace=str(self.workspace))
        self.registry = registry or ProviderRegistry(config, self.workspace)
        self.checkpoints = CheckpointStore(db)
        self.exec_log = ExecutionLog(db)
        self.edit_engine = EditEngine(
            self.workspace,
            checkpoints=self.checkpoints,
            new_file_eol=config.editor.new_file_eol,
            new_file_encoding=config.editor.new_file_encoding,
            rewrite_threshold_lines=config.editor.rewrite_threshold_lines,
            emoji_policy=config.editor.emoji_policy_enabled,
            emoji_allow=frozenset(config.editor.emoji_allow),
            syntax_check=config.editor.syntax_check,
        )
        self.tools: ToolRegistry = build_registry()
        self.policy = PermissionPolicy(config.permissions, mode=config.permissions.default_mode)
        self.gate = VerificationGate(config.verification, self.exec_log)
        self.doom = DoomLoopDetector()
        self.hooks = HookRunner(config.hooks, policy=self.policy, workspace=self.workspace)
        self.hooks.add_default_hooks()
        self.memory = MemoryStore()
        self.repo_map = RepoMap(self.workspace)
        self.history: list[Message] = []
        self.todos: list[dict[str, Any]] = []
        self.state = SessionState()
        self._approvals: dict[str, asyncio.Future] = {}
        self._shell: PersistentShell | None = None
        self._stop_event = asyncio.Event()
        self._current_llm_task: asyncio.Task | None = None
        self.model_ref = config.default_model
        self.agent_mode = "Agent"
        self.task_notes: dict[str, str] = {}
        self._register_external_tools()

    # -- infrastructure -------------------------------------------------------
    def _register_external_tools(self) -> None:
        """External workflows (YAML registry of HTTP endpoints) become tools."""
        import httpx
        from jinja2 import Template

        for et in self.config.external_tools:
            spec = ToolSpec(name=et.name, description=et.description, parameters=et.parameters or {"type": "object", "properties": {}})

            async def handler(ctx: ToolContext, args: dict[str, Any], _et=et) -> Any:
                body_tpl = Template(_et.input_template)
                body_tpl.globals["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
                rendered = body_tpl.render(inputs=args, **args)
                try:
                    payload = json.loads(rendered) if rendered.strip().startswith(("{", "[")) else rendered
                except json.JSONDecodeError:
                    payload = rendered
                pc = self.config.find_provider(_et.provider_id) if _et.provider_id else None
                base = (pc.base_url if pc else "").rstrip("/")
                url = _et.endpoint if _et.endpoint.startswith("http") else f"{base}/{_et.endpoint.lstrip('/')}"
                try:
                    async with httpx.AsyncClient(timeout=_et.timeout_seconds) as client:
                        headers = dict(_et.headers)
                        if pc is not None:
                            prov = self.registry.get(pc.id)
                            tokens = getattr(prov, "tokens", None)
                            if tokens is not None:
                                await tokens.apply_auth(headers)
                        resp = await client.request(_et.method, url, json=payload if isinstance(payload, (dict, list)) else None,
                                                     content=None if isinstance(payload, (dict, list)) else str(payload), headers=headers)
                        if resp.status_code >= 400:
                            return {"status": "error", "error": f"workflow returned HTTP {resp.status_code}", "why": resp.text[:300],
                                    "how_to_fix": "check the external_tools entry in config.yaml"}
                        from ..providers.generic_http import extract_path

                        data = resp.json()
                        result = extract_path(data, _et.result_path) if _et.result_path not in ("", "$") else data
                        return {"status": "ok", "result": result}
                except Exception as exc:
                    return {"status": "error", "error": f"workflow call failed: {exc}", "why": "HTTP error",
                            "how_to_fix": "check endpoint and auth in config.yaml"}

            from ..tools import Tool

            self.tools.register(Tool(name=et.name, description=et.description, parameters=spec.parameters, handler=handler, group="workflow"))

    def _tool_ctx(self) -> ToolContext:
        ctx = ToolContext(
            workspace=self.workspace,
            edit_engine=self.edit_engine,
            session_id=self.session_id,
            execute=self._execute,
            ask_user=self._ask_user,
            search_web=self._search_web,
            fetch_web=self._fetch_web,
            read_logs=self._read_logs,
            search_sessions=self._search_sessions,
            finish_task_cb=self._finish_task,
            todo_sink=self._set_todos,
        )
        ctx.extra["artifacts_config"] = getattr(self.config, "artifacts", None)
        ctx.extra["imagegen"] = self._generate_image
        return ctx

    async def _generate_image(self, prompt: str, dest, args: dict[str, Any]) -> dict[str, Any]:
        """Generate an image through the CONFIGURED gateway (img_llm tool)."""
        from ..artifacts.llm_image import generate_image as gateway_generate

        ig = getattr(self.config.artifacts, "image_generation", None) if hasattr(self.config, "artifacts") else None
        if ig is None or not getattr(ig, "provider_id", ""):
            raise ValueError(
                "no image-generation provider configured: add artifacts.image_generation "
                "{provider_id, model} to config.yaml (the provider must expose an "
                "OpenAI-compatible /images/generations endpoint)"
            )
        pc = self.config.find_provider(ig.provider_id)
        if pc is None:
            raise ValueError(f"artifacts.image_generation.provider_id {ig.provider_id!r} not found in providers")
        provider = self.registry.get(pc.id)
        headers: dict[str, str] = dict(pc.raw.get("headers") or {})
        tokens = getattr(provider, "tokens", None)
        if tokens is not None:
            await tokens.apply_auth(headers)
        endpoint = ig.endpoint or (pc.base_url or "")
        return await gateway_generate(
            dest,
            prompt,
            base_url=endpoint,
            headers=headers,
            model=ig.model or "",
            size=str(args.get("size") or ig.size),
            timeout=float(ig.timeout_seconds),
            style_hint=str(args.get("style_hint", "")),
            extra_body=dict(ig.extra_body or {}),
        )

    # -- callbacks used by tools ----------------------------------------------
    async def _execute(self, command: str, kind: str = "bash", cwd: str = ".", timeout: float = 120, background: bool = False) -> ExecResult:
        run_id = self.exec_log.start(kind=kind, command=command, cwd=str(self.workspace / cwd) if cwd else str(self.workspace), session_id=self.session_id, task_id=self.gate.state.task_id)
        await self._emit({"type": "tool_output_live", "run_id": run_id, "chunk": f"$ {command}\n"})
        t0 = time.time()
        if background:
            target_dir = self.workspace / cwd if cwd and cwd != "." else self.workspace
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(target_dir), env=subprocess_utf8_env(),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            rec = ExecResult(exit_code=0, stdout=f"started in background (pid {proc.pid})", run_id=run_id, background=True, pid=proc.pid, duration_s=0.0)
            self.exec_log.finish(run_id, 0, rec.stdout)
            return rec
        if kind in ("bash", "shell") and cwd in (".", "", str(self.workspace)):
            if self._shell is None:
                self._shell = PersistentShell(self.workspace, windows=sys.platform.startswith("win"))
            result = await self._shell.run(command, timeout=timeout)
        else:
            target_dir = self.workspace / cwd if cwd and cwd != "." else self.workspace
            try:
                proc = await asyncio.create_subprocess_shell(
                    command, cwd=str(target_dir), env=subprocess_utf8_env(),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    result = ExecResult(
                        exit_code=proc.returncode or 0,
                        stdout=out_b.decode("utf-8", errors="replace"),
                        stderr=err_b.decode("utf-8", errors="replace"),
                        duration_s=time.time() - t0,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    result = ExecResult(exit_code=124, stdout="", stderr="command timed out", timed_out=True, duration_s=time.time() - t0)
            except Exception as exc:
                result = ExecResult(exit_code=1, stderr=str(exc), duration_s=time.time() - t0)
        result.run_id = run_id
        self.exec_log.finish(run_id, result.exit_code, (result.stdout or "") + (result.stderr or ""))
        await self._emit({"type": "tool_output_live", "run_id": run_id, "chunk": truncate((result.stdout or "") + (result.stderr or ""), 2000), "done": True})
        return result

    async def _ask_user(self, question: str) -> str:
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        qid = new_id("q-")
        self._approvals[qid] = fut
        await self._emit({"type": "ask_user", "id": qid, "question": question})
        self.state.status = "waiting for your approval"
        await self._emit({"type": "status", "status": self.state.status})
        try:
            answer = await asyncio.wait_for(fut, timeout=3600)
        except asyncio.TimeoutError:
            answer = "(no answer - timeout)"
        self.state.status = "running"
        return str(answer)

    async def request_approval(self, action: str, detail: str) -> bool:
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        qid = new_id("a-")
        self._approvals[qid] = fut
        await self._emit({"type": "approval", "id": qid, "action": action, "detail": truncate(detail, 1500)})
        self.state.status = "waiting for your approval"
        await self._emit({"type": "status", "status": self.state.status})
        try:
            answer = await asyncio.wait_for(fut, timeout=3600)
        except asyncio.TimeoutError:
            return False
        return str(answer).lower() in ("approve", "yes", "true", "1")

    async def resolve_prompt_reply(self, reply_id: str, value: str) -> None:
        fut = self._approvals.pop(reply_id, None)
        if fut is not None and not fut.done():
            fut.set_result(value)

    async def _search_web(self, query: str) -> list[dict[str, Any]]:
        from .websearch import search_web  # local import: configured providers

        return await search_web(self.config.search, query)

    async def _fetch_web(self, url: str) -> str:
        from .websearch import fetch_markdown

        return await fetch_markdown(self.config.web, url)

    def _read_logs(self, level: str | None = None, query: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        from ..logging_setup import ring

        return ring().snapshot(limit=limit, level=level, query=query)

    def _search_sessions(self, query: str) -> list[dict[str, Any]]:
        return self.db.search_sessions(query)

    def _set_todos(self, todos: list[dict[str, Any]]) -> None:
        self.todos = todos
        self.db.set_todos(self.session_id, todos)
        with contextlib.suppress(RuntimeError):
            asyncio.get_event_loop().create_task(self._emit({"type": "todos", "todos": todos}))

    async def _finish_task(self, report: dict[str, Any]) -> dict[str, Any]:
        self.state.status = "verifying"
        await self._emit({"type": "status", "status": "running on_stop verification"})
        # claimed deliverables (generated documents/data) are verified on disk
        artifact_checks: list[Any] = []
        if report.get("artifacts"):
            artifact_checks = self.gate.check_artifacts(list(report.get("artifacts") or []), self.workspace)
            for c in artifact_checks:
                await self._emit({"type": "artifact_check", "ok": c.ok, "message": c.message})
        # on_stop hook: run project verification commands before accepting.
        verify_cmds = list(self.config.verification.commands)
        agents_md = self.workspace / "AGENTS.md"
        if agents_md.is_file():
            parsed = parse_agents_md(agents_md.read_text(encoding="utf-8", errors="replace"))
            for c in parsed["verify_commands"]:
                if c not in verify_cmds:
                    verify_cmds.append(c)
        hook_results: list[dict[str, Any]] = []
        if self.config.verification.on_stop_hook and verify_cmds:
            hook_results = await self.hooks.run_stop_verification(verify_cmds)
            for hr in hook_results:
                rid = self.exec_log.start(kind="verify", command=hr["command"], session_id=self.session_id, task_id=self.gate.state.task_id)
                self.exec_log.finish(rid, int(hr["exit_code"]), str(hr.get("output_excerpt", "")))
                hr["run_id"] = rid
            if all(int(hr.get("exit_code", 1)) == 0 for hr in hook_results):
                self.gate.note_verification_success([hr["run_id"] for hr in hook_results])
            else:
                self.gate.mark_dirty("on_stop verification failed")
        report = dict(report)
        if hook_results:
            report.setdefault("verification_runs", []).extend(
                [{"run_id": hr.get("run_id"), "command": hr["command"], "exit_code": hr["exit_code"]} for hr in hook_results]
            )
        verdict = self.gate.evaluate_finish(report, artifact_checks=artifact_checks)
        await self._emit({"type": "finish_verdict", **verdict})
        return verdict

    # -- messaging ------------------------------------------------------------
    async def _emit(self, event: dict[str, Any]) -> None:
        self.state.last_event_at = time.time()
        with contextlib.suppress(Exception):
            await self.emit(event)

    def _messages_for_history(self, content: str, role: str = "user", attachments: list[Attachment] | None = None) -> None:
        self.history.append(Message(role=role, content=content, attachments=attachments or []))
        meta = {"attachments": [{"kind": a.kind, "mime": a.mime, "name": a.name} for a in attachments]} if attachments else None
        self.db.add_message(self.session_id, role, content, meta=meta)

    # -- external content wrapping (injection defense) ------------------------
    @staticmethod
    def wrap_untrusted(source: str, content: str) -> str:
        return INJECTION_NOTE.format(source=source, content=truncate(content, 8000))

    # -- main loop ------------------------------------------------------------
    async def run_turn(
        self,
        user_text: str,
        model_ref: str = "",
        agent_mode: str | None = None,
        permission_mode: str | None = None,
        attachments: list[Attachment] | None = None,
    ) -> dict[str, Any]:
        set_correlation(new_id("cid-"))
        self.state.interrupted = False
        self._stop_event.clear()
        self.state.status = "thinking"
        if agent_mode:
            self.agent_mode = agent_mode
        if permission_mode:
            self.policy.set_mode(permission_mode)
        if model_ref:
            self.model_ref = model_ref
        await self._emit({"type": "status", "status": self.state.status, "model": self.model_ref, "mode": self.agent_mode})
        await self._emit({"type": "heartbeat"})

        # session_start hook
        hook_res = await self.hooks.run("session_start", {"session_id": self.session_id, "workspace": str(self.workspace)})
        if not hook_res.allowed:
            await self._emit({"type": "error", "message": hook_res.message})
            return {"status": "blocked", "message": hook_res.message}

        self._messages_for_history(user_text, "user", attachments)
        self.gate.start_task(new_id("task-"))
        self.gate.set_criteria([])
        self.doom.reset()
        try:
            self.repo_map.refresh()
        except Exception:
            pass

        try:
            result = await self._loop()
        except Exception as exc:
            log.exception("agent loop crashed")
            await self._emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            result = {"status": "error", "message": str(exc)}
        finally:
            if self._shell is not None:
                await self._shell.stop()
                self._shell = None
        self.state.status = "done" if result.get("status") != "error" else "error"
        await self._emit({"type": "done", "result": result, "usage": {
            "input_tokens": self.state.input_tokens,
            "output_tokens": self.state.output_tokens,
            "steps": self.state.steps,
        }})
        return result

    def _system_prompt(self) -> str:
        skills = []
        with contextlib.suppress(Exception):
            from .skills import SkillRegistry

            skills = SkillRegistry(self.workspace).listing_for_prompt()
        conventions = ""
        agents_md = self.workspace / "AGENTS.md"
        if agents_md.is_file():
            conventions = parse_agents_md(agents_md.read_text(encoding="utf-8", errors="replace"))["conventions"]
        provider, model_id = self._resolve_model(self.model_ref)
        profile = None
        found = self.config.find_model(self.model_ref) if self.model_ref else None
        if found:
            profile = found[1]
        edit_format = edit_format_for_model(profile, "hashline" if (self.config.editor.hashline_for_weak_models and profile and profile.weak) else "str_replace")
        if profile and profile.weak and self.config.editor.hashline_for_weak_models:
            edit_format = "hashline"
        return build_system_prompt(
            edit_format=edit_format,
            weak_model=bool(profile and profile.weak),
            permission_mode=self.policy.mode,
            conventions=conventions,
            skills=skills,
            memory_summary=self.memory.summary_for_prompt(),
            repo_map=self.repo_map.render(focus_files=set(self.todos and [] or ()), token_budget=1500),
            notes=self.task_notes.get("notes", ""),
            agent_mode=self.agent_mode,
            artifacts=self.agent_mode not in ("Plan", "Chat") and getattr(self.config.artifacts, "enabled", True),
        )

    def _resolve_model(self, model_ref: str):
        return self.registry.resolve(model_ref)

    def _tool_specs(self) -> list[ToolSpec]:
        specs = []
        for t in self.tools.specs():
            specs.append(ToolSpec(name=t["name"], description=t["description"], parameters=t["parameters"]))
        if self.agent_mode == "Plan":
            readonly = {"fs_read", "fs_list", "fs_glob", "fs_grep", "search_sessions", "web_search", "todo_write", "ask_user"}
            specs = [s for s in specs if s.name in readonly]
        elif self.agent_mode == "Chat":
            specs = [s for s in specs if s.name in ("fs_read", "fs_list", "fs_glob", "fs_grep", "web_search", "ask_user")]
        return specs

    async def _loop(self) -> dict[str, Any]:
        cfg = self.config.limits
        tools = self._tool_specs()
        ctx = self._tool_ctx()
        final_text: list[str] = []

        for step in range(cfg.max_steps):
            if self._stop_event.is_set() or self.state.interrupted:
                return {"status": "stopped", "message": "stopped by user"}
            self.state.steps = step + 1
            self.state.status = "thinking"
            await self._emit({"type": "status", "status": "thinking", "step": self.state.steps})

            provider, model_id = self._resolve_model(self.model_ref)
            messages = [Message(role="system", content=self._system_prompt())] + self.history

            # -- call the model (streamed) -----------------------------------
            self.state.status = "writing response"
            await self._emit({"type": "status", "status": self.state.status, "step": self.state.steps})
            acc_text: list[str] = []
            native_calls: list[Any] = []

            async def on_event(ev: Any) -> None:
                if ev.type == "text_delta":
                    acc_text.append(ev.text)
                    await self._emit({"type": "token", "text": ev.text})
                elif ev.type == "tool_call" and ev.tool_call is not None:
                    native_calls.append(ev.tool_call)

            try:
                self._current_llm_task = asyncio.current_task()
                result: ChatResult | None = None
                # Step-level resilience: transient provider errors (rate limit,
                # 5xx, connection reset, timeout) are retried with backoff so a
                # single gateway blip does not kill the whole turn. Providers
                # already retry internally; this is the last-resort layer.
                step_retries = max(0, int(getattr(self.config.limits, "llm_step_retries", 2)))
                for attempt in range(step_retries + 1):
                    try:
                        result = await provider.chat(
                            messages, tools=tools, model=model_id,
                            temperature=0.2, max_tokens=None, stream=None, on_event=on_event,
                        )
                        break
                    except LLMError as exc:
                        if not getattr(exc, "retryable", False) or attempt >= step_retries:
                            raise
                        wait_s = min(2.0 * (2 ** attempt), 15.0)
                        await self._emit({"type": "warning", "message": (
                            f"provider transient error (attempt {attempt + 1}/{step_retries + 1}), "
                            f"retrying in {wait_s:.0f}s: {exc}")})
                        await self._emit({"type": "status", "status": "retrying after provider error"})
                        await asyncio.sleep(wait_s)
                assert result is not None
            except LLMError as exc:
                await self._emit({"type": "error", "message": str(exc), "provider_error": exc.to_dict()})
                return {"status": "error", "message": str(exc)}
            except asyncio.CancelledError:
                return {"status": "stopped", "message": "stopped by user"}
            finally:
                self._current_llm_task = None

            self.state.input_tokens += result.usage.input_tokens
            self.state.output_tokens += result.usage.output_tokens
            await self._emit({"type": "usage", "input_tokens": self.state.input_tokens,
                              "output_tokens": self.state.output_tokens, "step": self.state.steps})

            text = "".join(acc_text) if acc_text else result.text
            calls = list(result.tool_calls) or list(native_calls)
            # textual tool calls (weak models / native_tools: false)
            if not calls and text:
                parsed, issues = parse_tool_calls(text)
                if parsed:
                    calls = []
                    for i, pc in enumerate(parsed):
                        from ..providers.base import ToolCall

                        calls.append(ToolCall(id=f"call_{i}", name=pc.name, arguments=pc.arguments))
                    text = extract_reply_text(text)
                elif issues:
                    issue = issues[0]
                    self.history.append(Message(role="assistant", content=text))
                    self.history.append(Message(role="user", content="Tool call parse error:\n" + issue.error + "\nWHY: " + issue.why + "\nHOW TO FIX: " + issue.how_to_fix))
                    continue

            if text.strip():
                self.state.status = "writing response" if calls else "done"
                self._messages_for_history(text, "assistant")
                final_text.append(text)
                ev = self.doom.note_assistant_text(text)
                if ev and ev.action == "stop":
                    await self._emit({"type": "warning", "message": ev.message})
                    return {"status": "stopped", "message": ev.message}

            if not calls:
                # Plain text answer - not finished as a task unless user asked a question.
                return {"status": "done", "text": "\n".join(final_text), "steps": self.state.steps}

            # -- run tool calls ------------------------------------------------
            self.history.append(Message(role="assistant", content=text, tool_calls=calls))
            for call in calls:
                if self._stop_event.is_set():
                    return {"status": "stopped", "message": "stopped by user"}
                outcome = await self._run_tool(ctx, call.name, call.arguments, call.id)
                self.history.append(Message(role="tool", content=json.dumps(outcome, ensure_ascii=False, default=str),
                                            tool_call_id=call.id, name=call.name))
                ok = isinstance(outcome, dict) and outcome.get("status") in ("ok", "finished")
                ev = self.doom.note_tool_result(ok, call_key=json.dumps({"n": call.name, "a": call.arguments}, sort_keys=True))
                if ev:
                    await self._emit({"type": "warning", "message": ev.message})
                    if ev.action == "stop":
                        return {"status": "stopped", "message": ev.message}
                    if ev.action == "escalate":
                        strong = self._stronger_model()
                        if strong:
                            self.model_ref = strong
                            await self._emit({"type": "escalate", "model": strong, "message": ev.message})
                if isinstance(outcome, dict) and outcome.get("finished"):
                    return {"status": "finished", "result": outcome}
            # tool-result clearing: compress processed outputs in history
            self._compact_tool_results()
            await self._emit({"type": "heartbeat"})
            # context compaction near the limit
            if self.state.input_tokens + self.state.output_tokens > self.config.limits.max_tokens_per_task * 0.8:
                await self._compact_history()

        return {"status": "limit_reached", "message": f"step limit ({cfg.max_steps}) reached - unfinished"}

    async def _run_tool(self, ctx: ToolContext, name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
        self.state.status = f"running {name}"
        self.state.current_tool = name
        await self._emit({"type": "status", "status": self.state.status, "tool": name})
        await self._emit({"type": "tool_start", "call_id": call_id, "tool": name, "arguments": truncate(json.dumps(arguments, ensure_ascii=False, default=str), 1200)})
        t0 = time.time()

        # hooks: pre_tool_use
        hook = await self.hooks.run("pre_tool_use", {"tool": name, "arguments": arguments, "approved": False})
        if not hook.allowed:
            outcome: dict[str, Any] = {"status": "error", "error": hook.message or "blocked",
                                       "why": "blocked by pre_tool_use hook", "how_to_fix": "comply with the hook message"}
            if hook.message.startswith("APPROVAL"):
                ok = await self.request_approval(name, json.dumps(arguments, ensure_ascii=False, default=str))
                if ok:
                    self.policy.tainted = self.policy.tainted  # approval granted
                    hook = await self.hooks.run("pre_tool_use", {"tool": name, "arguments": arguments, "approved": True})
                    if hook.allowed:
                        outcome = await self.tools.dispatch(ctx, name, arguments)
                else:
                    outcome = {"status": "error", "error": "user declined this action", "why": "approval refused", "how_to_fix": "propose a different approach"}
            await self._emit({"type": "tool_end", "call_id": call_id, "tool": name, "result": truncate(json.dumps(outcome, ensure_ascii=False, default=str), 4000), "duration_s": round(time.time() - t0, 2)})
            return outcome

        tool = self.tools.get(name)
        # validate arguments against schema; auto-repair once for weak models
        if tool is not None:
            issue = validate_arguments(name, arguments, tool.parameters)
            if issue is not None:
                outcome = {"status": "error", "error": issue.error, "why": issue.why, "how_to_fix": issue.how_to_fix}
                await self._emit({"type": "tool_end", "call_id": call_id, "tool": name, "result": json.dumps(outcome, ensure_ascii=False), "duration_s": 0})
                return outcome

        outcome = await self.tools.dispatch(ctx, name, arguments)

        # mark dirty after edits (verification gate)
        if name in ("fs_edit", "fs_write", "fs_multi_edit", "fs_apply_patch", "fs_hashline_edit",
                    "edit_file", "write_file", "multi_edit", "apply_patch", "hashline_edit"):
            if isinstance(outcome, dict) and outcome.get("status") == "ok":
                self.gate.mark_dirty(f"edit via {name}")
                self.doom.note_edit(str(arguments.get("path", "")))
                await self._emit({"type": "dirty", **self.gate.status()})

        hook = await self.hooks.run("post_tool_use", {"tool": name, "arguments": arguments,
                                                      "path": arguments.get("path", "")})
        if hook.output.strip() and isinstance(outcome, dict):
            outcome.setdefault("warnings", [])
            if isinstance(outcome["warnings"], list):
                outcome["warnings"].append(truncate(hook.output.strip(), 1500))

        rendered = json.dumps(outcome, ensure_ascii=False, default=str)
        await self._emit({"type": "tool_end", "call_id": call_id, "tool": name,
                          "result": truncate(rendered, 4000), "duration_s": round(time.time() - t0, 2)})
        return outcome if isinstance(outcome, dict) else {"status": "ok", "result": outcome}

    def _stronger_model(self) -> str:
        pairs = self.config.all_models()
        strong = [f"{p.id}/{m.id}" for p, m in pairs if not m.weak]
        return strong[0] if strong else self.model_ref

    def _compact_tool_results(self) -> None:
        """Replace fully processed raw tool outputs with short markers."""
        for i, m in enumerate(self.history):
            if m.role == "tool" and len(m.content) > 2000:
                m.content = json.dumps({
                    "status": "processed",
                    "note": f"tool result already processed by the model ({len(m.content)} chars) - see trace if needed",
                    "tool": m.name,
                }, ensure_ascii=False)

    async def _compact_history(self) -> None:
        """Summarize the conversation near the context limit (pre_compact hook)."""
        hook = await self.hooks.run("pre_compact", {"session_id": self.session_id})
        if not hook.allowed:
            return
        self.state.status = "compacting context"
        await self._emit({"type": "status", "status": self.state.status})
        try:
            provider, model_id = self._resolve_model(self.config.roles.summarizer or self.model_ref)
            transcript = "\n".join(f"{m.role}: {truncate(m.content, 400)}" for m in self.history[-40:])
            summary_res = await provider.chat(
                [
                    Message(role="system", content="Summarize this agent transcript preserving: decisions, touched files, pending tasks, acceptance criteria status. Be terse."),
                    Message(role="user", content=transcript),
                ],
                model=model_id, temperature=0.0, max_tokens=800,
            )
            summary = summary_res.text
            keep_tail = self.history[-6:]
            self.history = [
                Message(role="system", content=f"[compacted summary of earlier work]\n{summary}")
            ] + keep_tail
            await self._emit({"type": "compacted", "summary": truncate(summary, 500)})
        except Exception as exc:
            log.warning("compaction failed", extra={"data": {"error": str(exc)}})

    # -- control --------------------------------------------------------------
    async def stop(self) -> None:
        self.state.interrupted = True
        self._stop_event.set()
        if self._shell is not None:
            await self._shell.stop()
        task = self._current_llm_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        await self._emit({"type": "status", "status": "stopped"})

    # -- sub-agent (isolated context) ------------------------------------------
    async def spawn_subagent(self, prompt: str, tools_subset: tuple[str, ...] = CORE_TOOL_NAMES) -> str:
        """Run a nested loop with its own context; returns a short summary."""
        self.state.status = "running sub-agent"
        await self._emit({"type": "status", "status": "running sub-agent"})
        provider, model_id = self._resolve_model(self.config.roles.subagent or self.model_ref)
        ctx = self._tool_ctx()
        messages = [
            Message(role="system", content=build_system_prompt(edit_format="str_replace", agent_mode="Chat")),
            Message(role="user", content=prompt),
        ]
        tools = [ToolSpec(**s) for s in self.tools.specs() if s["name"] in tools_subset]
        output_tail = ""
        for _ in range(12):
            res = await provider.chat(messages, tools=tools or None, model=model_id, temperature=0.1)
            if not res.has_tool_calls:
                output_tail = res.text
                break
            messages.append(Message(role="assistant", content=res.text, tool_calls=res.tool_calls))
            for tc in res.tool_calls:
                outcome = await self.tools.dispatch(ctx, tc.name, tc.arguments)
                messages.append(Message(role="tool", content=truncate(json.dumps(outcome, ensure_ascii=False, default=str), 2500),
                                        tool_call_id=tc.id, name=tc.name))
        return truncate(output_tail or "(sub-agent produced no text)", 2000)
