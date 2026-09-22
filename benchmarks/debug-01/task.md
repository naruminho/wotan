# Task debug-01: crash on empty input (repro first!)

`report.py` crashes with IndexError on an empty day list. **Reproduce first**
(`python report.py` with `days=[]`), then fix minimally and add a regression
test.

## Acceptance criteria
- `python report.py` exits 0 and prints `average: n/a` for empty input
- Regression test added; suite passes
