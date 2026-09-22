from pathlib import Path
import re, sys
ws = Path(sys.argv[1])
text = (ws / "review.md").read_text(encoding="utf-8").lower() if (ws / "review.md").is_file() else ""
assert len(text) > 100, "review.md missing or too thin"
for topic in ["error", "race", "rollback", "timeout"]:
    if topic in text or ("concurren" in text and topic == "race") or ("thread" in text and topic == "race"):
        continue
    if topic in text:
        continue
    assert False, f"review.md does not mention {topic}"
assert text.count("-") >= 3 or text.count("*") >= 3, "no findings list"
print("PASS")
