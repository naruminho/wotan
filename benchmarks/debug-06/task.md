# Task debug-06: performance regression

`dedupe.py` finds duplicates with a nested O(n^2) loop; the test with 20k
items times out (5s budget). Fix it to O(n) keeping behavior identical.

## Acceptance criteria
- Tests pass within the time budget, unmodified
