"""Permission modes and destructive-action policy.

Modes: ``ask`` (default), ``edits`` (auto-approve edits only), ``autonomous``.
Destructive commands (rm/del/format, git push/reset --hard, package installs,
external network sends) ALWAYS require approval regardless of mode; a
configurable allow/deny list can override. The Rule of Two: once a session is
tainted by external content, external state changes need approval even in
autonomous mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import PermissionsConfig
from ..logging_setup import get_logger

log = get_logger("wotan.agent.permissions", component="agent")

MODES = ("ask", "edits", "autonomous")

_DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("recursive_delete", re.compile(r"\b(rm\s+|del\s+/|rmdir\s+/|rmdir\s+|shutil\.rmtree)", re.IGNORECASE)),
    ("format_disk", re.compile(r"\b(format\s+[a-z]:|mkfs\b)", re.IGNORECASE)),
    ("git_history_rewrite", re.compile(r"\bgit\s+(push\s+.*--force|reset\s+--hard|clean\s+(-\w*f\w*|--force))", re.IGNORECASE)),
    ("git_push", re.compile(r"\bgit\s+push\b", re.IGNORECASE)),
    ("package_install", re.compile(r"\b(pip3?\s+install|python\s+-m\s+pip\s+install|npm\s+install|yarn\s+add|pnpm\s+add)\b", re.IGNORECASE)),
    ("registry_publish", re.compile(r"\b(npm\s+publish|twine\s+upload)\b", re.IGNORECASE)),
    ("system_config", re.compile(r"\b(reg\s+add|schtasks\s+/create|bcdedit|diskpart)\b", re.IGNORECASE)),
    ("env_secret_read", re.compile(r"(type|cat|Get-Content)\s+.*(\.env|id_rsa|\.pem)\b", re.IGNORECASE)),
]

_NETWORK_SEND = re.compile(r"\b(curl|wget|Invoke-WebRequest|requests\.(post|put|patch)|httpx\.(post|put|patch))\b", re.IGNORECASE)
_PROTECTED_EDIT = re.compile(r"(pyproject\.toml|setup\.cfg|setup\.py|tox\.ini|pytest\.ini|tsconfig\.json|package\.json|\.eslintrc|biome\.json|oxlint\.json|\.pre-commit-config\.yaml)$")
_NO_VERIFY = re.compile(r"--no-verify")


@dataclass
class PermissionDecision:
    allowed: bool
    needs_approval: bool
    reason: str = ""
    rule: str = ""


@dataclass
class PermissionPolicy:
    config: PermissionsConfig = field(default_factory=PermissionsConfig)
    mode: str = "ask"
    tainted: bool = False  # Rule of Two: external content was processed

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown permission mode {mode!r}; expected one of {MODES}")
        self.mode = mode

    def _rule_match(self, patterns: list[str], text: str) -> str | None:
        for pat in patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    return pat
            except re.error:
                if pat.lower() in text.lower():
                    return pat
        return None

    def check_command(self, command: str, tool_name: str = "shell_exec") -> PermissionDecision:
        deny = self._rule_match(self.config.always_deny, command)
        if deny:
            return PermissionDecision(False, False, f"blocked by deny-list rule: {deny}", "deny_list")
        allow = self._rule_match(self.config.always_allow, command)
        if _NO_VERIFY.search(command):
            return PermissionDecision(False, True, "'--no-verify' is blocked: verification hooks must run", "pre_tool_use:block_no_verify")
        for kind, pat in _DESTRUCTIVE_PATTERNS:
            if pat.search(command):
                if allow:
                    log.warning(
                        "auto-approved destructive command (allow-listed)",
                        extra={"data": {"kind": kind, "command": command, "rule": allow}},
                    )
                    return PermissionDecision(True, False, f"destructive but allow-listed ({kind})", f"destructive:{kind}")
                return PermissionDecision(True, True, f"destructive action ({kind}) requires approval", f"destructive:{kind}")
        if _NETWORK_SEND.search(command) and self.tainted:
            return PermissionDecision(True, True, "session is tainted by external content: external network send needs approval (Rule of Two)", "rule_of_two")
        if _NETWORK_SEND.search(command) and self.mode == "autonomous" and not self.tainted:
            log.warning("auto-approved network command (autonomous mode)", extra={"data": {"command": command}})
            return PermissionDecision(True, False, "network command in autonomous mode", "autonomous")
        if allow:
            return PermissionDecision(True, False, "allow-listed", "allow_list")
        if self.mode == "ask":
            return PermissionDecision(True, True, "permission mode 'ask': commands require approval", "mode:ask")
        return PermissionDecision(True, False, f"permission mode '{self.mode}'", f"mode:{self.mode}")

    def check_edit(self, path: str) -> PermissionDecision:
        name = path.replace("\\", "/").split("/")[-1]
        if any(name == p or path.endswith(p) for p in self.config.protected_files) or _PROTECTED_EDIT.search(path.replace("\\", "/")):
            return PermissionDecision(
                False, True,
                "config/lint/test configuration files are protected: fix the code, not the config",
                "pre_tool_use:protect_config",
            )
        if self.mode == "ask":
            return PermissionDecision(True, True, "permission mode 'ask': file edits require approval", "mode:ask")
        return PermissionDecision(True, False, f"permission mode '{self.mode}'", f"mode:{self.mode}")

    # file-producing artifact tools follow the same edit policy as fs_write
    _ARTIFACT_WRITERS = frozenset({
        "doc_pdf", "doc_docx", "doc_xlsx", "doc_pptx",
        "data_csv", "data_synthetic", "data_chart", "img_transform",
    })

    def check_tool(self, tool_name: str, arguments: dict) -> PermissionDecision:
        """Generic gate used by pre_tool_use hooks."""
        if tool_name in ("shell_exec", "bash", "shell", "run_command", "py_run", "run_python"):
            return self.check_command(str(arguments.get("command") or arguments.get("code") or arguments.get("script") or ""), tool_name)
        if tool_name in ("fs_edit", "fs_write", "fs_multi_edit", "fs_apply_patch", "fs_hashline_edit",
                         "edit_file", "write_file", "multi_edit", "apply_patch", "hashline_edit"):
            return self.check_edit(str(arguments.get("path", "")))
        if tool_name in self._ARTIFACT_WRITERS:
            return self.check_edit(str(arguments.get("path", "")))
        if tool_name == "img_satellite":
            # network tile fetch: approval in ask mode, Rule of Two when tainted
            if self.tainted:
                return PermissionDecision(True, True, "session is tainted by external content: satellite tile fetch needs approval (Rule of Two)", "rule_of_two")
            if self.mode == "ask":
                return PermissionDecision(True, True, "permission mode 'ask': network fetches require approval", "mode:ask")
            return PermissionDecision(True, False, f"permission mode '{self.mode}'", f"mode:{self.mode}")
        return PermissionDecision(True, False, "read-only or trusted tool", "default")
