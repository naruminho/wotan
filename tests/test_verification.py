"""Verification gate: evidence cross-check, dirty tracking, anti-cheating."""

from __future__ import annotations

from wotan.agent.execution_log import ExecutionLog
from wotan.agent.verification import VerificationGate
from wotan.config import VerificationConfig


def make_gate(db, **kw) -> VerificationGate:
    cfg = VerificationConfig(**kw)
    return VerificationGate(cfg, ExecutionLog(db))


def test_evidence_matches_real_runs(db):
    gate = make_gate(db)
    gate.start_task("task-1", [{"criterion": "tests pass", "how_to_test": "pytest"}])
    log = gate.exec_log
    rid = log.start("bash", "python -m pytest -q", session_id="s", task_id="task-1")
    log.finish(rid, 0, "12 passed")

    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [{"criterion": "tests pass", "status": "passed", "run_id": rid}],
        "commands": [{"run_id": rid, "command": "python -m pytest -q", "exit_code": 0, "output_excerpt": "12 passed"}],
    })
    assert verdict["finished"] is True
    assert gate.state.status == "finished"


def test_claimed_command_never_run_is_refused(db):
    gate = make_gate(db)
    gate.start_task("task-2")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [],
        "commands": [{"command": "python -m pytest -q", "exit_code": 0}],
    })
    assert verdict["finished"] is False
    assert "never executed" in verdict["error"]["why"]
    assert gate.state.status == "refused"


def test_faked_exit_code_is_refused(db):
    gate = make_gate(db)
    gate.start_task("task-3")
    rid = gate.exec_log.start("bash", "pytest", session_id="s", task_id="task-3")
    gate.exec_log.finish(rid, 1, "FAILED")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [],
        "commands": [{"run_id": rid, "command": "pytest", "exit_code": 0}],
    })
    assert verdict["finished"] is False
    assert "exited with 1" in verdict["error"]["why"] or "real run" in verdict["error"]["why"]


def test_failed_command_blocks_even_if_claimed(db):
    gate = make_gate(db)
    gate.start_task("task-4")
    rid = gate.exec_log.start("bash", "pytest", session_id="s", task_id="task-4")
    gate.exec_log.finish(rid, 2, "error")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [],
        "commands": [{"run_id": rid, "command": "pytest", "exit_code": 2}],
    })
    assert verdict["finished"] is False


def test_dirty_since_verification_blocks(db):
    gate = make_gate(db)
    gate.start_task("task-5")
    rid = gate.exec_log.start("bash", "pytest", session_id="s", task_id="task-5")
    gate.exec_log.finish(rid, 0, "ok")
    gate.note_verification_success([rid])
    gate.mark_dirty("edit after test")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [],
        "commands": [{"run_id": rid, "command": "pytest", "exit_code": 0}],
    })
    assert verdict["finished"] is False
    assert "unverified changes" in verdict["error"]["why"]


def test_criteria_require_evidence(db):
    gate = make_gate(db)
    gate.start_task("task-6", [{"criterion": "it works"}])
    rid = gate.exec_log.start("bash", "pytest", session_id="s", task_id="task-6")
    gate.exec_log.finish(rid, 0, "ok")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [{"criterion": "it works", "status": "passed"}],  # no evidence
        "commands": [{"run_id": rid, "command": "pytest", "exit_code": 0}],
    })
    assert verdict["finished"] is False
    assert "without evidence" in verdict["error"]["why"]


def test_missing_criteria_reported(db):
    gate = make_gate(db)
    gate.start_task("task-7", [{"criterion": "a"}, {"criterion": "b"}])
    rid = gate.exec_log.start("bash", "true", session_id="s", task_id="task-7")
    gate.exec_log.finish(rid, 0, "")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [{"criterion": "a", "status": "passed", "run_id": rid}],
        "commands": [{"run_id": rid, "command": "true", "exit_code": 0}],
    })
    assert verdict["finished"] is False
    assert "'b'" in verdict["error"]["why"]


def test_anti_cheat_detects_test_tampering(db):
    gate = make_gate(db)
    diff = (
        "+++ b/tests/test_thing.py\n"
        "+@pytest.mark.skip\n"
        "+# assert result == expected\n"
        "+assert True\n"
    )
    problems = gate.scan_test_tampering(diff)
    assert len(problems) >= 3
    kinds = " ".join(problems)
    assert "skip_marker" in kinds and "commented_assert" in kinds and "weak_assert" in kinds


def test_not_verified_status_allowed_with_reason(db):
    gate = make_gate(db)
    gate.start_task("task-8", [{"criterion": "real gateway", "how_to_test": "manual"}])
    rid = gate.exec_log.start("bash", "pytest", session_id="s", task_id="task-8")
    gate.exec_log.finish(rid, 0, "ok")
    verdict = gate.evaluate_finish({
        "summary": "done",
        "acceptance_criteria": [{"criterion": "real gateway", "status": "not_verified", "evidence": "no access to the real gateway"}],
        "commands": [{"run_id": rid, "command": "pytest", "exit_code": 0}],
    })
    assert verdict["finished"] is True  # honest 'not_verified' is acceptable


def test_execution_log_offloads_large_output(tmp_path, db, monkeypatch):
    from wotan.paths import state_dir

    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    log = ExecutionLog(db, offload_threshold=100)
    rid = log.start("bash", "big", session_id="s")
    log.finish(rid, 0, "x" * 5000)
    rec = log.get(rid)
    assert rec.output_file
    assert log.read_output(rid, limit=50)
