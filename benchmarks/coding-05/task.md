# Task coding-05: refactor without behavior change

`invoice.py` computes totals in one tangled function. Refactor: extract
`_line_total(qty, price)` and `_discount(rate, subtotal)` helpers.
All existing tests must keep passing **unmodified**.

## Acceptance criteria
- Tests pass unmodified
- The two helper functions exist
