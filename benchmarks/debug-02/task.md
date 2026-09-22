# Task debug-02: silent wrong output (logic bug)

The failing tests in `test_total.py` describe the expected discount rule:
10% off when subtotal >= 100, else none. `total.py` computes it wrongly.
Fix the code (not the tests).

## Acceptance criteria
- All tests pass **unmodified**
