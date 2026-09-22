from pathlib import Path
import sys
ws = Path(sys.argv[1])
readme = (ws / "templates" / "README.template.md").read_text(encoding="utf-8") if (ws / "templates" / "README.template.md").is_file() else ""
changelog = (ws / "templates" / "CHANGELOG.template.md").read_text(encoding="utf-8") if (ws / "templates" / "CHANGELOG.template.md").is_file() else ""
assert "pallet-tracker" in readme, "service name missing"
for sec in ["Overview", "Endpoints", "Configuration"]:
    assert sec.lower() in readme.lower(), f"README missing {sec}"
for sec in ["Unreleased", "Added", "Fixed"]:
    assert sec.lower() in changelog.lower(), f"CHANGELOG missing {sec}"
print("PASS")
