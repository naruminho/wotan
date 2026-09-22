from pathlib import Path
import sys
ws = Path(sys.argv[1])
text = (ws / "report.md").read_text(encoding="utf-8") if (ws / "report.md").is_file() else ""
assert "8" in text, "total count wrong/missing"
assert "2" in text, "error count wrong/missing"
assert "/api/shipments" in text, "top endpoint missing"
recs = [ln for ln in text.splitlines() if ln.strip().startswith(("-", "*", "1", "2", "3"))]
assert len(recs) >= 3, "less than 3 recommendations"
print("PASS")
