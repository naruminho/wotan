import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stdout + p.stderr

src = (ws / "payments.py").read_text(encoding="utf-8")
assert "PaymentError" in src, "PaymentError not defined"
tests = (ws / "test_serve.py").read_text(encoding="utf-8") if False else (ws / "test_payments.py").read_text(encoding="utf-8")
assert "PaymentError" in tests, "failure path not tested"
ok, out = run_tests()
assert ok, out
print("PASS")
