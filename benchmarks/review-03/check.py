import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stdout + p.stderr

tests = (ws / "test_guard.py").read_text(encoding="utf-8")
assert "assert True" not in tests, "weakened assertion left in place"
assert "raises" in tests and "ValueError" in tests, "real assertion not restored"
ok, out = run_tests()
assert ok, out
print("PASS")
