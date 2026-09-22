from pathlib import Path
import sys
ws = Path(sys.argv[1])
text = (ws / "MEMORY.md").read_text(encoding="utf-8") if (ws / "MEMORY.md").is_file() else ""
assert "Solaris" in text, "product name missing"
assert "summary" in text.lower(), "summary section missing"
for term in ["manifest", "lane", "SLA"]:
    assert term.lower() in text.lower(), f"term missing: {term}"
assert "open" in text.lower() and "question" in text.lower(), "open questions missing"
print("PASS")
