# Task review-05: swallowed errors

`payments.py` returns `None` when charging fails, hiding outages. Change it to
raise `PaymentError` (with the cause message) and update/add tests.

## Acceptance criteria
- Failures raise `PaymentError`
- Tests pass and cover the failure path
