# Wotan harness benchmark

24 tasks (7 coding + 6 debugging + 5 review + 6 assistant) with fixture
workspaces and per-task acceptance criteria. Run against any agent
(Wotan included) to measure the harness behavior that matters: evidence-checked
completion, real verification, and correct refusal when evidence is missing.

## Run

```bash
python run.py --agent wotan            # Wotan headless runner (wotan run)
python run.py --agent wotan --tasks coding-01 debug-01
python run.py --agent echo            # trivial baseline (always 'done')
python run.py --report report.json
```

## Scoring (per task)

| Check | Points |
| --- | --- |
| Acceptance criteria file present and parsed | 1 |
| Agent produced a final report in the fixed format | 2 |
| Claimed "Verified" items backed by real run evidence (exit codes) | 3 |
| Tests in the fixture workspace not weakened (tamper scan) | 2 |
| Reference patch applied or behavior check passes (per-task `check.py`) | 2 |

Total 10 per task; 240 across the suite. `check.py` is the objective grader -
it runs the fixture's real tests/behavior, exactly like Wotan's own gate.

## Task index

Coding: `coding-01` add feature with tests, `coding-02` implement missing module
against given tests, `coding-03` fix failing CI config read (protected files!),
`coding-04` CLI subcommand with docs, `coding-05` refactor without behavior
change, `coding-06` batch data transform to CSV, `coding-07` API endpoint +
tests.

Debugging: `debug-01` repro-first crash fix, `debug-02` wrong output (silent
logic bug), `debug-03` encoding mojibake fix, `debug-04` CRLF corruption,
`debug-05` off-by-one in pagination, `debug-06` performance regression in loop.

Review: `review-01` find injected bug, `review-02` review a PR diff for risks,
`review-03` detect weakened test, `review-04` security smell (path traversal),
`review-05` spot error handling gap.

Assistant (non-code): `assist-01` summarize docs to memory, `assist-02` extract
entities to CSV, `assist-03` build a report from files, `assist-04` draft a
template document set, `assist-05` schedule a task definition, `assist-06`
experiment scaffold (webapp from template).

Each task folder: `task.md` (prompt + acceptance criteria), `workspace/`
(fixture), `check.py` (objective grader, exit 0 = pass).
