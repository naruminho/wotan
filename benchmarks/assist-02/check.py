import csv
from pathlib import Path
import sys
ws = Path(sys.argv[1])
rows = list(csv.DictReader((ws / "entities.csv").open(encoding="utf-8"))) if (ws / "entities.csv").is_file() else []
assert rows, "entities.csv missing or empty"
assert {"file", "text", "type"} <= set(rows[0].keys()), rows[0].keys()
texts = {r["text"] for r in rows}
for needed in ["Diana Costa", "Nordwind Logistics", "USD 4,300", "Pedro Rocha"]:
    assert needed in texts, f"missing entity {needed}: {texts}"
by_text = {r["text"]: r["type"] for r in rows}
assert by_text["Diana Costa"] == "person" and by_text["Pedro Rocha"] == "person"
assert by_text["Nordwind Logistics"] == "organization"
assert by_text["USD 4,300"] == "money"
print("PASS")
