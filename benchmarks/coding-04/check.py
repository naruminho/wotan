import subprocess, sys
from pathlib import Path
ws = Path(sys.argv[1])

def run_tests():
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", str(ws)], capture_output=True, text=True, timeout=120)
    return p.returncode == 0, p.stdout + p.stderr

import subprocess, sys
p = subprocess.run([sys.executable, str(ws / "tool.py"), "greet", "Wotan"], capture_output=True, text=True, timeout=30)
assert p.stdout.strip() == "Hello, Wotan!", p.stdout + p.stderr
p2 = subprocess.run([sys.executable, str(ws / "tool.py"), "count", "a", "b"], capture_output=True, text=True, timeout=30)
assert p2.stdout.strip() == "2", p2.stdout
assert "greet" in (ws / "README.md").read_text(encoding="utf-8").lower()
print("PASS")
