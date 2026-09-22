"""Entity extraction experiment (working example, mock-gateway tested).

Extracts structured entities (people, organizations, locations, money) from
text/PDF files into output/results.csv via extract_json (schema + repair loop).

Run: python run.py --input input
Test without the real gateway: wotan mock   (then set GATEWAY_URL=http://127.0.0.1:8787)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path

from wotan.gateway_client import chat, chat_with_document, extract_json

log = logging.getLogger("experiment.entity_extraction")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "type": {"type": "string", "enum": ["person", "organization", "location", "money", "other"]},
                },
                "required": ["text", "type"],
            },
        }
    },
    "required": ["entities"],
}

PROMPT = (
    "Extract the entities from this document (people, organizations, "
    "locations, money amounts). Return JSON with an 'entities' array of "
    "{text, type} objects."
)


async def extract_file(path: Path) -> list[dict]:
    if path.suffix.lower() == ".pdf":
        answer = await chat_with_document(PROMPT, path)
    else:
        # Text-like files: send the text directly (cheaper and inspectable).
        text = path.read_text(encoding="utf-8", errors="replace")
        answer = await chat(f"{PROMPT}\n\nDocument ({path.name}):\n{text}")
    data = await extract_json(answer, schema=SCHEMA)
    return [{"file": path.name, **e} for e in data.get("entities", [])]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="input")
    parser.add_argument("--output", default="output/results.csv")
    args = parser.parse_args()

    in_dir = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in (".txt", ".md", ".pdf"))
    rows: list[dict] = []
    for f in files:
        try:
            rows.extend(await extract_file(f))
            log.info("ok: %s", f.name)
        except Exception as exc:
            log.error("failed: %s: %s", f.name, exc)
            rows.append({"file": f.name, "text": f"ERROR: {exc}", "type": "other"})

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "text", "type"])
        writer.writeheader()
        writer.writerows(rows)
    log.info("wrote %s (%d entities)", out_path, len(rows))
    print(f"[OK] {len(rows)} entities -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
