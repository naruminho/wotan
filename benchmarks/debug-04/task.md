# Task debug-04: CRLF file corrupted by the tool

`save_note.py` rewrites `note.txt` and destroys its CRLF line endings.
The failing test requires CRLF preserved. Fix the code, not the test.

## Acceptance criteria
- After `python save_note.py`, `note.txt` still uses CRLF for every line
- Tests pass unmodified
