import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stderr

ok, out = run_tests()
src = (ws / "calc.py").read_text(encoding="utf-8")
tests = (ws / "test_calc.py").read_text(encoding="utf-8")
assert "def power" in src, "power missing"
assert "test_power" in tests, "tests not added"
assert ok, out
print("PASS")
