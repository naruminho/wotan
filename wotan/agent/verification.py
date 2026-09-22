"""The completion gate: the harness decides when a task may end, not the model.

``finish_task`` requires an evidence report. The gate:

* tracks a "dirty since last verification" flag (any edit after the last
  successful test invalidates verification),
* cross-checks every claimed command against the real :class:`ExecutionLog`
  (a claimed command that did not run, or ran and failed, is refused),
* runs the ``on_stop`` verification commands (from AGENTS.md / config) before
  accepting completion,
* supports anti-cheating checks on test files (skip/xfail/weakened asserts).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import VerificationConfig
from ..logging_setup import get_logger
from ..util import ToolError, utc_iso
from .execution_log import ExecutionLog

log = get_logger("wotan.agent.verification", component="verification")


@dataclass
class AcceptanceCriterion:
    criterion: str
    how_to_test: str = ""


@dataclass
class VerificationState:
    task_id: str = ""
    criteria: list[AcceptanceCriterion] = field(default_factory=list)
    dirty: bool = False
    last_verified_at: float | None = None
    last_verified_runs: list[str] = field(default_factory=list)
    edits_since_verify: int = 0
    status: str = "in_progress"  # in_progress | finished | refused


ANTI_CHEAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("skip_marker", re.compile(r"@(?:pytest\.)?(?:mark\.)?(?:skip|skipif|xfail)\b")),
    ("skip_call", re.compile(r"\bskip\s*\(\s*\)")),
    ("commented_assert", re.compile(r"^\s*#\s*assert\b", re.MULTILINE)),
    ("weak_assert", re.compile(r"\bassert\s+True\b")),
    ("bare_try", re.compile(r"try\s*:\s*\n\s*pass\s*\n\s*except")),
]


@dataclass
class EvidenceCheck:
    ok: bool
    message: str


class VerificationGate:
    def __init__(self, config: VerificationConfig, exec_log: ExecutionLog) -> None:
        self.config = config
        self.exec_log = exec_log
        self.state = VerificationState()
        self._claim_command = True  # run claimed commands on_stop via hooks if not run yet

    # -- task setup ----------------------------------------------------------
    def start_task(self, task_id: str, criteria: list[dict[str, Any]] | None = None) -> None:
        # A new turn does not clean the workspace: carry over the dirty state and
        # the last verification point from the previous turn.
        prev = self.state
        self.state = VerificationState(
            task_id=task_id,
            criteria=[AcceptanceCriterion(c.get("criterion", ""), c.get("how_to_test", "")) for c in criteria or []],
            status="in_progress",
            dirty=prev.dirty,
            last_verified_at=prev.last_verified_at,
            last_verified_runs=list(prev.last_verified_runs),
            edits_since_verify=prev.edits_since_verify,
        )

    def set_criteria(self, criteria: list[dict[str, Any]]) -> None:
        self.state.criteria = [AcceptanceCriterion(c.get("criterion", ""), c.get("how_to_test", "")) for c in criteria]

    def mark_dirty(self, reason: str = "edit") -> None:
        if self.config.dirty_tracking:
            self.state.dirty = True
            self.state.edits_since_verify += 1
            log.info("verification marked dirty", extra={"data": {"reason": reason, "edits_since": self.state.edits_since_verify}})

    def note_verification_success(self, run_ids: list[str]) -> None:
        self.state.dirty = False
        self.state.edits_since_verify = 0
        self.state.last_verified_at = time.time()
        self.state.last_verified_runs = list(run_ids)
        self.exec_log.mark_verified(run_ids)

    # -- evidence checking ---------------------------------------------------
    def _check_claimed_commands(self, claims: list[dict[str, Any]]) -> list[EvidenceCheck]:
        checks: list[EvidenceCheck] = []
        for claim in claims:
            cmd = str(claim.get("command", ""))
            claimed_exit = claim.get("exit_code")
            run_id = claim.get("run_id", "")
            rec = self.exec_log.get(run_id) if run_id else None
            if rec is None:
                # fall back: find a run with the same command
                runs = self.exec_log.list(session_id="", task_id=self.state.task_id)
                matches = [r for r in runs if r.command.strip() == cmd.strip() and r.finished]
                if not matches:
                    checks.append(EvidenceCheck(
                        ok=False,
                        message=(
                            f"claimed command was never executed according to the execution log: {cmd!r}. "
                            "The harness refuses completion. Run the command through shell_exec/py_run first."
                        ),
                    ))
                    continue
                rec = matches[0]
            if rec.exit_code is None:
                checks.append(EvidenceCheck(ok=False, message=f"command {cmd!r} has not finished yet (run {rec.id})"))
                continue
            if int(claimed_exit) != int(rec.exit_code):
                checks.append(EvidenceCheck(
                    ok=False,
                    message=(
                        f"claimed exit code {claimed_exit} for {cmd!r} but the real run {rec.id} "
                        f"exited with {rec.exit_code}. The harness does not trust the report."
                    ),
                ))
                continue
            if int(rec.exit_code) != 0 and not claim.get("acceptable_failure", False):
                checks.append(EvidenceCheck(
                    ok=False,
                    message=f"command {cmd!r} failed with exit code {rec.exit_code} - completion refused until it passes",
                ))
                continue
            checks.append(EvidenceCheck(ok=True, message=f"verified run {rec.id}: {cmd!r} exit {rec.exit_code}"))
        return checks

    def _check_dirty(self) -> EvidenceCheck:
        if self.config.dirty_tracking and self.state.dirty:
            return EvidenceCheck(
                ok=False,
                message=(
                    f"There are unverified changes since the last test "
                    f"({self.state.edits_since_verify} edits after the last verification). "
                    "Run verification before finishing."
                ),
            )
        return EvidenceCheck(ok=True, message="no unverified changes")

    def scan_test_tampering(self, diff_text: str) -> list[str]:
        """Highlight suspicious changes inside test files (strict mode blocks)."""
        problems: list[str] = []
        in_test_file = False
        for line in diff_text.splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                in_test_file = bool(re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", line)) or "/tests/" in line
                continue
            if in_test_file and (line.startswith("+") or line.startswith("-")):
                body = line[1:]  # strip the diff marker before pattern matching
                for kind, pat in ANTI_CHEAT_PATTERNS:
                    if pat.search(body):
                        problems.append(f"{kind}: {line[:120]}")
        return problems

    def evaluate_finish(self, report: dict[str, Any], on_stop_commands: list[str] | None = None) -> dict[str, Any]:
        """Validate a finish_task evidence report. Returns verdict for the model."""
        problems: list[str] = []
        claims = report.get("commands") or []
        hook_results = report.get("verification_runs") or []
        checks = self._check_claimed_commands(claims)
        problems.extend(c.message for c in checks if not c.ok)

        # At least one piece of hard evidence is required: executed commands
        # (verified against the log) or on_stop verification runs - unless every
        # criterion is honestly reported as not_verified/skipped with a reason.
        if not claims and not hook_results:
            all_honest = bool(report.get("acceptance_criteria")) and all(
                str(c.get("status")) in ("not_verified", "skipped") and c.get("evidence")
                for c in report.get("acceptance_criteria") or []
            )
            if not all_honest:
                problems.append(
                    "no evidence: finish_task must cite commands that actually ran (with run_id and exit codes) "
                    "or report every criterion as 'not_verified' with a reason"
                )

        criteria = report.get("acceptance_criteria") or []
        if self.state.criteria and not criteria:
            problems.append("finish_task must report the status of every acceptance criterion")
        expected = {c.criterion for c in self.state.criteria}
        reported = {str(c.get("criterion", "")) for c in criteria}
        missing = expected - reported
        if missing:
            problems.append(f"missing status for acceptance criteria: {sorted(missing)}")
        for c in criteria:
            if c.get("status") == "passed" and not c.get("evidence") and not c.get("run_id"):
                problems.append(
                    f"criterion {c.get('criterion')!r} claims 'passed' without evidence - "
                    "attach a run_id or an output excerpt"
                )
            if c.get("run_id"):
                rec = self.exec_log.get(str(c["run_id"]))
                if rec is None:
                    problems.append(f"criterion {c.get('criterion')!r} references unknown run {c['run_id']!r}")
                elif rec.exit_code not in (0, None) and c.get("status") == "passed":
                    problems.append(
                        f"criterion {c.get('criterion')!r} claims passed but run {rec.id} exited {rec.exit_code}"
                    )

        dirty = self._check_dirty()
        if not dirty.ok:
            problems.append(dirty.message)

        # on_stop verification commands (the hook runs them; results checked here if provided)
        for hr in hook_results:
            checks = self._check_claimed_commands([hr])
            problems.extend(c.message for c in checks if not c.ok)

        if problems:
            self.state.status = "refused"
            verdict = {
                "finished": False,
                "error": ToolError(
                    error="completion refused by the verification gate",
                    why="\n- ".join([""] + problems),
                    how_to_fix=(
                        "run the verification commands (tests/lint/type-check) via shell_exec, fix failures, "
                        "and call finish_task again with evidence that matches the execution log. "
                        "If a criterion cannot be verified report it as 'not_verified' with the reason."
                    ),
                ).to_dict(),
                "checks": [{"ok": c.ok, "message": c.message} for c in checks],
            }
            log.warning("finish refused", extra={"data": {"problems": problems[:5]}})
            return verdict

        self.state.status = "finished"
        self.note_verification_success([str(c.get("run_id")) for c in claims if c.get("run_id")])
        return {
            "finished": True,
            "summary": report.get("summary", ""),
            "accepted_at": utc_iso(),
            "checks": [{"ok": c.ok, "message": c.message} for c in checks],
        }

    def status(self) -> dict[str, Any]:
        return {
            "task_id": self.state.task_id,
            "dirty": self.state.dirty,
            "edits_since_verify": self.state.edits_since_verify,
            "last_verified_at": self.state.last_verified_at,
            "criteria": [{"criterion": c.criterion, "how_to_test": c.how_to_test} for c in self.state.criteria],
            "status": self.state.status,
        }
