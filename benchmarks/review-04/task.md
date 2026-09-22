# Task review-04: security smell - path traversal

`serve.py` reads `files/<name>` for any client-supplied `name` - a traversal
vulnerability (`../../etc/passwd`). Fix it (reject names escaping `files/`)
and add a test.

## Acceptance criteria
- Traversal names are rejected (exception or error result)
- Legit names still work
- Test added; suite passes
