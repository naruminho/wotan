import subprocess
import sys
from pathlib import Path

ws = Path(sys.argv[1])

probe = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from serve import read_file\n"
    "try:\n"
    "    read_file('../serve.py')\n"
    "    print('VULN')\n"
    "except Exception:\n"
    "    print('SAFE')\n"
)
p = subprocess.run(
    [sys.executable, "-c", probe, str(ws)],
    capture_output=True,
    text=True,
    timeout=30,
    cwd=str(ws),
)
assert "SAFE" in p.stdout, "traversal still allowed"
tests = (ws / "test_serve.py").read_text(encoding="utf-8")
assert "traversal" in tests.lower() or "../" in tests, "no traversal test added"
q = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", str(ws)],
    capture_output=True,
    text=True,
    timeout=120,
)
assert q.returncode == 0, q.stdout + q.stderr
print("PASS")
