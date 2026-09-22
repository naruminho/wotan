import csv, subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])
p = subprocess.run([sys.executable, str(ws / "transform.py")], cwd=str(ws), capture_output=True, text=True, timeout=60)
assert p.returncode == 0, p.stdout + p.stderr
out = ws / "out.csv"
assert out.is_file(), "out.csv missing"
rows = list(csv.DictReader(out.open(encoding="utf-8")))
got = {r["name"]: float(r["total"]) for r in rows}
assert got == {"alpha": 11.0, "beta": 2.0, "gamma": 5.0}, got
assert [r["name"] for r in rows] == ["alpha", "beta", "gamma"], "not sorted"
print("PASS")
