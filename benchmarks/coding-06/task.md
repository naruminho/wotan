# Task coding-06: batch data transform to CSV

`data/` holds `a.csv` and `b.csv` (columns `name,amount`). Write
`transform.py` that merges them into `out.csv` sorted by `name` with an extra
`total` column = sum of amounts per name, then **run it** to produce `out.csv`.

## Acceptance criteria
- `python transform.py` produces `out.csv`
- Rows sorted by name; `total` correct per name
