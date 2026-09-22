# schedule.yaml schema

name: string
schedule: cron string (5 fields)
command: string
timeout_minutes: int
retries: int
notify_on: success | failure | always | never
