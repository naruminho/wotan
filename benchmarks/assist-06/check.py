from pathlib import Path
import subprocess, sys
ws = Path(sys.argv[1])
root = ws / "experiments" / "pallet_extract"
for name in ["README.md", "run.py", "requirements.txt", ".env.example"]:
    assert (root / name).is_file(), f"missing {name}"
run_py = (root / "run.py").read_text(encoding="utf-8")
assert "--port" in run_py, "--port missing"
assert "pallet" in run_py.lower(), "goal not adapted"
assert "run" in (root / "README.md").read_text(encoding="utf-8").lower(), "README lacks run instructions"
p = subprocess.run([sys.executable, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(root / "run.py")], capture_output=True, text=True, timeout=30)
assert p.returncode == 0, "run.py does not parse: " + p.stderr
print("PASS")
