"""The shipped entity_extraction experiment must actually work end-to-end.

Verifies the deliverable 'one mock-working example (PDF entity extraction)':
runs the batch script against the mock gateway and checks real CSV output.
"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from wotan.paths import PACKAGE_DIR

from conftest import MockServer, mock_identity_cfg  # noqa: F401  (fixtures)

EXPERIMENT = PACKAGE_DIR.parent / "experiments" / "entity_extraction"


@pytest.mark.asyncio
async def test_entity_extraction_experiment_runs_against_mock(
    mock_server: MockServer, mock_identity_cfg: dict, tmp_path: Path
) -> None:
    assert (EXPERIMENT / "run.py").is_file()
    assert (EXPERIMENT / "webapp.py").is_file()

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "providers": [mock_identity_cfg],
                "default_provider": "fictional",
                "default_model": "fictional/mock-chat",
                "models": [{"id": "mock-chat", "weak": False}],
            }
        ),
        encoding="utf-8",
    )
    out_csv = tmp_path / "results.csv"
    env = dict(
        os.environ,
        WOTAN_CONFIG=str(config_file),
        WOTAN_HOME=str(tmp_path / "home"),
        PYTHONPATH=str(PACKAGE_DIR.parent),
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(EXPERIMENT / "run.py"),
            "--input",
            str(EXPERIMENT / "input"),
            "--output",
            str(out_csv),
        ],
        cwd=str(EXPERIMENT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert out_csv.is_file()
    rows = list(csv.DictReader(out_csv.open(encoding="utf-8")))
    assert rows, "no entities extracted"
    assert {r["file"] for r in rows} == {"sample.txt"}
    types = {r["type"] for r in rows}
    assert "organization" in types or "person" in types, rows
    texts = " ".join(r["text"] for r in rows).lower()
    assert "acme" in texts or "maria" in texts, rows
