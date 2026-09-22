# Task assist-05: define a scheduled task

Write `schedule.yaml` describing a daily 07:30 task that runs
`python scripts/weekly_report.py --out reports/` with a 30 minute timeout,
retries 2, and notification on failure. Follow the schema in `schema.md`.

## Acceptance criteria
- schedule.yaml parses and matches the schema keys
- Correct schedule, command, timeout, retries and notification settings
