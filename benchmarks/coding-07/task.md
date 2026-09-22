# Task coding-07: add an API endpoint with tests

`service.py` routes dict requests. Add route `multiply` (`{"a":..,"b":..}`
-> `{"result": a*b}`), return `{"error": ...}` for unknown routes, and add
tests in `test_service.py`.

## Acceptance criteria
- `handle_request({"route": "multiply", "a": 3, "b": 4})` -> `{"result": 12}`
- Unknown route returns an `error` key
- Tests pass
