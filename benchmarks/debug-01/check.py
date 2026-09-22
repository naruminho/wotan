import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stderr

import subprocess, sys
p = subprocess.run([sys.executable, str(ws / "report.py")], capture_output=True, text=True, timeout=30)
assert p.returncode == 0, "still crashes: " + p.stderr
assert "average: n/a" in p.stdout, p.stdout
tests = (ws / "test_report.py").read_text(encoding="utf-8")
assert "def test_" in tests and "empty" in tests.lower(), "no empty-input regression test"
ok, out = run_tests()
assert ok, out
print("PASS")
