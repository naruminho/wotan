# AGENTS.md

Short conventions for the agent. Keep this file under ~60 lines: pointers and
commands, not prose. Wotan loads it automatically at session start.

## Conventions

- Python 3.11+, type hints on public functions, pathlib for paths.
- Frontend: TypeScript strict, no new runtime dependencies without need.
- Tests are part of the deliverable: fix the code, never the tests.
- No emoji or decorative Unicode in code, logs or commits - use [OK], [ERROR], ->.
- Portuguese and other accented text uses correct diacritics.

## Layout

- `wotan/` backend package (FastAPI server, providers, edit engine, agent).
- `frontend/` React + Vite UI (build into `wotan/static/` with `scripts/build_frontend.py`).
- `tests/` pytest suite; `frontend/src/test/` vitest suite.
- `experiments/` generated GenAI experiments (templates in `experiments/templates/`).

## Commands

- Install: `pip install -e ".[dev]"` and `python scripts/build_frontend.py`
- Run: `wotan` (or `python -m wotan`)
- Mock gateway: `wotan mock` (identity API + fictional contract on :8787)
- Diagnose gateway config: `wotan doctor`

## Verify

- `python -m pytest -q`
- `cd frontend && npx vitest run`
- `python scripts/build_frontend.py --skip-install`

## Edit rules

- Targeted edits only (old_string/new_string); no whole-file rewrites.
- Read before edit; keep encodings and line endings as they are.
