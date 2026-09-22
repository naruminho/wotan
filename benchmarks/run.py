#!/usr/bin/env python3
"""Wotan harness benchmark runner.

Spawns an agent per task against the fixture workspace, then scores the
result with the objective grader (check.py) plus harness checks (report
format, evidence, test-tamper scan).

Usage:
    python run.py --agent wotan
    python run.py --agent echo --tasks coding-01 --report out.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
TASKS = sorted(p.parent.name for p in HERE.glob("*/task.md"))
REPORT_RE = re.compile(
    r"##\s*Verified.*?##\s*Not verified.*?##\s*Known issues.*?##\s*How to test",
    re.IGNORECASE | re.DOTALL,
)


def run_agent(agent: str, task_dir: Path, workspace: Path, prompt: str) -> tuple[str, float]:
    t0 = time.time()
    if agent == "echo":
        return (
            "# Echo agent\n"
            "## Verified\n- (nothing - baseline)\n"
            "## Not verified\n- all\n"
            "## Known issues\n- none\n"
            "## How to test\n- n/a\n",
            time.time() - t0,
        )
    if agent == "wotan":
        proc = subprocess.run(
            [sys.executable, "-m", "wotan", "run", prompt, str(workspace)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        return proc.stdout + proc.stderr, time.time() - t0
    raise SystemExit(f"unknown agent: {agent} (use wotan | echo)")


def score(task_dir: Path, workspace: Path, transcript: str) -> dict:
    checks = {}
    checks["acceptance_criteria"] = (task_dir / "task.md").is_file()
    checks["report_format"] = bool(REPORT_RE.search(transcript))
    checks["claims_verified_section"] = bool(re.search(r"##\s*Verified", transcript, re.I))
    # Evidence: any exit-code or command output mentioned near 'Verified'.
    checks["evidence_mentions"] = bool(re.search(r"exit|passed|\[OK\]|0 failed", transcript, re.I))
    # Anti-cheating: fixture tests must still exist and pass unchanged count.
    test_files = list(workspace.rglob("test_*.py")) + list(workspace.rglob("*_test.py"))
    checks["tests_present"] = len(test_files) > 0
    grader = task_dir / "check.py"
    if grader.is_file():
        proc = subprocess.run(
            [sys.executable, str(grader), str(workspace)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        checks["grader_passed"] = proc.returncode == 0
    else:
        checks["grader_passed"] = False
    points = (
        int(checks["acceptance_criteria"])
        + 2 * int(checks["report_format"])
        + 3 * int(checks["evidence_mentions"] and checks["claims_verified_section"])
        + 2 * int(checks["tests_present"] and checks["grader_passed"] if checks["tests_present"] else checks["grader_passed"])
        + 2 * int(checks["grader_passed"])
    )
    return {"checks": checks, "points": points}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", default="wotan", choices=["wotan", "echo"])
    parser.add_argument("--tasks", nargs="*", default=None)
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    tasks = args.tasks or TASKS
    results = []
    for name in tasks:
        task_dir = HERE / name
        if not (task_dir / "task.md").is_file():
            print(f"[skip] {name}: no task.md")
            continue
        prompt = (task_dir / "task.md").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix=f"wotan-bench-{name}-") as td:
            workspace = Path(td) / "workspace"
            shutil.copytree(task_dir / "workspace", workspace)
            transcript, secs = run_agent(args.agent, task_dir, workspace, prompt)
            result = score(task_dir, workspace, transcript)
        print(f"[{name}] {result['points']}/10 in {secs:.1f}s  {result['checks']}")
        results.append({"task": name, "seconds": secs, **result})

    total = sum(r["points"] for r in results)
    print(f"\nTOTAL: {total}/{10 * len(results)} over {len(results)} tasks")
    if args.report:
        Path(args.report).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
