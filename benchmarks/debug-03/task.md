# Task debug-03: mojibake names

`names.txt` shows `Jo?o` / `A?lia` style corruption and `read_names()`
decodes wrong. Repair so `read_names()` returns the correct Portuguese names
with diacritics (`João`, `Amélia`, `Conceição`) and add a test.

## Acceptance criteria
- `read_names()` returns the three names with correct diacritics
- Test added; suite passes
