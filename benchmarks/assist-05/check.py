import sys
from pathlib import Path
import yaml
ws = Path(sys.argv[1])
data = yaml.safe_load((ws / "schedule.yaml").read_text(encoding="utf-8")) if (ws / "schedule.yaml").is_file() else None
assert data, "schedule.yaml missing"
assert set(data) >= {"name", "schedule", "command", "timeout_minutes", "retries", "notify_on"}, data.keys()
assert "30 7" in str(data["schedule"]) or "07:30" in str(data["schedule"]) or str(data["schedule"]).startswith("30 7"), data["schedule"]
assert "weekly_report.py" in str(data["command"])
assert int(data["timeout_minutes"]) == 30
assert int(data["retries"]) == 2
assert data["notify_on"] in ("failure", "always")
print("PASS")
