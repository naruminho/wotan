"""Web UI variant of the entity extraction experiment.

Run: python webapp.py --port 8899
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from wotan.gateway_client import chat, chat_with_document, extract_json

from run import SCHEMA, PROMPT  # reuse the batch logic definitions

log = logging.getLogger("experiment.entity_extraction.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Experiment: entity extraction")
HERE = Path(__file__).parent


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (HERE / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> JSONResponse:
    try:
        data = await file.read()
        name = file.filename or "upload.txt"
        tmp = HERE / "output" / name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        if name.lower().endswith(".pdf"):
            answer = await chat_with_document(PROMPT, tmp)
        else:
            text = data.decode("utf-8", errors="replace")
            answer = await chat(f"{PROMPT}\n\nDocument ({name}):\n{text}")
        result = await extract_json(answer, schema=SCHEMA)
        log.info("extracted from %s (%d bytes)", name, len(data))
        return JSONResponse({"ok": True, "entities": result.get("entities", [])})
    except Exception as exc:
        log.exception("extract failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
