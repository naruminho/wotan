# Task review-03: a test was weakened - restore it

`test_guard.py` contains a weakened assertion (`assert True` placeholder) for
the documented rule: `parse_age` must reject negative ages with ValueError.
Restore the real assertion and fix `guard.py` if needed.

## Acceptance criteria
- No `assert True` remains in the tests
- Tests enforce the documented rule and pass
