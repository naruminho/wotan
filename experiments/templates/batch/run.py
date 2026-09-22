"""Batch experiment template: process a folder of files into CSV/Excel.

Run: python run.py --input ./input --output ./output/results.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path

from wotan.gateway_client import chat_with_document, extract_json

log = logging.getLogger("experiment.batch")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": ["invoice", "contract", "report", "other"]},
    },
    "required": ["summary", "category"],
}

PROMPT = "Classify and summarize this document in one sentence."


async def process_file(path: Path) -> dict:
    text = await chat_with_document(PROMPT, path)
    data = await extract_json(text, schema=SCHEMA)
    return {"file": path.name, "summary": data.get("summary", ""), "category": data.get("category", "other")}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="input")
    parser.add_argument("--output", default="output/results.csv")
    args = parser.parse_args()

    in_dir = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_run = out_path.parent / "run.log"

    files = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in (".txt", ".md", ".pdf", ".csv"))
    log.info("processing %d files from %s", len(files), in_dir)
    rows = []
    for f in files:
        try:
            rows.append(await process_file(f))
            log.info("ok: %s", f.name)
        except Exception as exc:
            log.error("failed: %s: %s", f.name, exc)
            rows.append({"file": f.name, "summary": f"ERROR: {exc}", "category": "other"})

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "summary", "category"])
        writer.writeheader()
        writer.writerows(rows)
    log.info("wrote %s (%d rows)", out_path, len(rows))


if __name__ == "__main__":
    asyncio.run(main())
