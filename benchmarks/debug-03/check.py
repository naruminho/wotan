import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stderr

from read_names import read_names
names = read_names(str(ws / "names.txt"))
assert names == ["João", "Amélia", "Conceição"], names
tests = (ws / "test_names.py").read_text(encoding="utf-8")
assert "João" in tests or "joao" in tests.lower(), "no diacritics test"
ok, out = run_tests()
assert ok, out
print("PASS")
