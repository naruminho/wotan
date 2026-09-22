import subprocess
import sys
from pathlib import Path

ws = Path(sys.argv[1])

p = subprocess.run(
    [sys.executable, "-m", "pytest", "-q", str(ws)],
    capture_output=True,
    text=True,
    timeout=120,
)
cfg = (ws / "settings.yaml").read_bytes()
assert cfg == b"timeout: 30" + b"\n", "settings.yaml was modified - fix the code, not the config"
assert p.returncode == 0, p.stdout + p.stderr
print("PASS")
