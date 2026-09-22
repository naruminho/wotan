# Task coding-04: add a CLI subcommand with docs

`tool.py` has a `count` subcommand. Add `greet <name>` printing
`Hello, <name>!` and document both commands in `README.md`.

## Acceptance criteria
- `python tool.py greet Wotan` prints exactly `Hello, Wotan!`
- README documents `greet`
- Existing `count` still works
