import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stderr

ok, out = run_tests()
src = (ws / "invoice.py").read_text(encoding="utf-8")
assert "def _line_total" in src and "def _discount" in src, "helpers not extracted"
assert ok, out
print("PASS")
