"""Configurable hook system (YAML + scripts), Claude-Code style.

Events: ``session_start``, ``pre_tool_use``, ``post_tool_use``, ``pre_compact``,
``stop``. A hook can BLOCK the action with exit code 2; its stdout is injected
into the agent's context. Default hooks:

* post_tool_use on edits: fast formatter/linter (ruff when installed) -
  "success is silent, failure is verbose",
* pre_tool_use: block destructive commands and protect config files
  ("fix the code, not the config"), block ``--no-verify``,
* stop: the verification gate (on_stop commands from AGENTS.md/config).
"""

from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import HookConfig, HooksConfig
from ..logging_setup import get_logger
from ..paths import subprocess_utf8_env
from .permissions import PermissionDecision, PermissionPolicy

log = get_logger("wotan.agent.hooks", component="hooks")


@dataclass
class HookResult:
    allowed: bool = True
    message: str = ""
    blocked_by: str = ""
    output: str = ""


DEFAULT_PRE_TOOL = HookConfig(
    event="pre_tool_use",
    command="",  # handled in-process (policy engine)
    matcher="",
)

POST_EDIT_LINTER = HookConfig(
    event="post_tool_use",
    command="python -m ruff check --fix --quiet {path}",
    matcher=r"^(fs_edit|fs_write|fs_multi_edit|edit_file|write_file|multi_edit|apply_patch|fs_apply_patch|hashline_edit|fs_hashline_edit)$",
    timeout_seconds=10.0,
)


class HookRunner:
    def __init__(self, config: HooksConfig, policy: PermissionPolicy | None = None, workspace: Path | None = None) -> None:
        self.config = config
        self.policy = policy
        self.workspace = workspace or Path.cwd()
        self._extra: list[HookConfig] = []

    def add_default_hooks(self) -> None:
        """Built-in defaults: post-edit linter (silent on success)."""
        self._extra.append(POST_EDIT_LINTER)

    def all_hooks(self) -> list[HookConfig]:
        return list(self.config.hooks) + list(self._extra)

    async def run(self, event: str, payload: dict[str, Any]) -> HookResult:
        if not self.config.enabled:
            return HookResult()
        result = HookResult()
        # Built-in policy for pre_tool_use (always on).
        if event == "pre_tool_use" and self.policy is not None:
            decision: PermissionDecision = self.policy.check_tool(str(payload.get("tool", "")), payload.get("arguments") or {})
            if not decision.allowed:
                result.allowed = False
                result.blocked_by = f"policy:{decision.rule}"
                result.message = (
                    f"ERROR: tool call blocked by hook ({decision.rule})\n"
                    f"WHY: {decision.reason}\n"
                    "HOW TO FIX: fix the code, not the config - change the source instead of pyproject.toml/tsconfig/test config"
                )
                return result
            if decision.needs_approval and not payload.get("approved"):
                result.allowed = False
                result.blocked_by = f"approval:{decision.rule}"
                result.message = f"APPROVAL REQUIRED: {decision.reason}"
                return result

        tool_name = str(payload.get("tool", ""))
        for hook in self.all_hooks():
            if hook.event != event:
                continue
            if hook.matcher and not re.search(hook.matcher, tool_name):
                continue
            if not hook.command:
                continue
            cmd = hook.command
            for k, v in payload.items():
                if isinstance(v, (str, int, float)):
                    cmd = cmd.replace("{" + k + "}", str(v))
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(self.workspace),
                    env=subprocess_utf8_env(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=hook.timeout_seconds)
                except asyncio.TimeoutError:
                    proc.kill()
                    out_b = b"hook timed out"
                out = out_b.decode("utf-8", errors="replace") if out_b else ""
                rc = proc.returncode or 0
                if rc == hook.block_exit_code:
                    result.allowed = False
                    result.blocked_by = f"hook:{hook.command[:40]}"
                    result.message = out.strip() or f"blocked by hook ({hook.command})"
                elif rc != 0:
                    # failure is verbose: inject output so the model can react
                    result.output += f"[hook {hook.command} exited {rc}]\n{out.strip()[:2000]}\n"
                else:
                    # success is silent - only keep non-empty output
                    if out.strip():
                        result.output += out.strip()[:2000] + "\n"
            except Exception as exc:
                result.output += f"[hook {hook.command} failed to run: {exc}]\n"
        return result

    async def run_stop_verification(self, commands: list[str]) -> list[dict[str, Any]]:
        """The stop hook: run project verification commands before accepting stop."""
        results: list[dict[str, Any]] = []
        for cmd in commands:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(self.workspace),
                    env=subprocess_utf8_env(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
                out = out_b.decode("utf-8", errors="replace") if out_b else ""
                results.append({"command": cmd, "exit_code": proc.returncode or 0, "output_excerpt": out[-1500:]})
            except asyncio.TimeoutError:
                results.append({"command": cmd, "exit_code": -1, "output_excerpt": "verification command timed out"})
            except Exception as exc:
                results.append({"command": cmd, "exit_code": -1, "output_excerpt": str(exc)})
        return results


def parse_agents_md(text: str) -> dict[str, Any]:
    """Extract conventions and verify commands from AGENTS.md.

    Recognized sections: ``## Verify`` (one command per line / bullet),
    ``## Conventions`` (injected into the system prompt), ``max_lines`` style
    hints. The file itself is loaded as project instructions at session start.
    """
    out: dict[str, Any] = {"verify_commands": [], "conventions": "", "raw": text}
    in_verify = False
    in_conv = False
    conv_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            header = stripped.lstrip("#").strip().lower()
            in_verify = header in ("verify", "verification", "test", "tests", "check", "checks")
            in_conv = header in ("conventions", "style", "guidelines")
            continue
        if in_verify:
            candidate = stripped.lstrip("-*").strip().strip("`")
            if candidate:
                out["verify_commands"].append(candidate)
        elif in_conv:
            conv_lines.append(line)
    out["conventions"] = "\n".join(conv_lines).strip()
    return out
