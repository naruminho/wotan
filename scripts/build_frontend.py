#!/usr/bin/env python3
"""Build the frontend and embed it in the backend package.

Produces ``wotan/static/`` (served by FastAPI) so a corporate install needs
only ``pip install`` - no npm on the target machine.

Usage:
    python scripts/build_frontend.py           # npm install (if needed) + vite build
    python scripts/build_frontend.py --skip-install
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
STATIC = ROOT / "wotan" / "static"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"[build] $ {' '.join(cmd)}  (in {cwd})")
    proc = subprocess.run(cmd, cwd=str(cwd))
    if proc.returncode != 0:
        print(f"[build] FAILED: {' '.join(cmd)}", file=sys.stderr)
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true", help="do not run npm install")
    args = parser.parse_args()

    npm = shutil.which("npm")
    if not npm:
        print("npm not found - install Node.js 20+ to build the frontend", file=sys.stderr)
        return 2

    if not args.skip_install and not (FRONTEND / "node_modules").is_dir():
        run([npm, "install", "--no-audit", "--no-fund"], FRONTEND)

    run([npm, "run", "build"], FRONTEND)

    dist = FRONTEND / "dist"
    if not dist.is_dir():
        print("frontend build produced no dist/", file=sys.stderr)
        return 1

    if STATIC.exists():
        shutil.rmtree(STATIC)
    shutil.copytree(dist, STATIC)

    # Logo at the package root as well (window icon / README).
    assets = ROOT / "wotan" / "assets"
    assets.mkdir(exist_ok=True)
    logo = FRONTEND / "public" / "logo.svg"
    if logo.is_file():
        shutil.copy(logo, assets / "logo.svg")

    n_files = sum(1 for _ in STATIC.rglob("*") if _.is_file())
    print(f"[build] embedded frontend -> wotan/static ({n_files} files)")
    print("[build] OK: 'wotan' now serves the UI without any runtime downloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
