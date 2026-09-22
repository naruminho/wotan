"""CSV and markdown-table writers (standard library only).

CSV: configurable delimiter/encoding (utf-8, utf-8-sig for Excel, latin-1)
with sniffable, Excel-friendly output. Also renders 2D rows as a GFM markdown
table for chat replies and documents.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

_ENCODINGS = {"utf-8", "utf-8-sig", "latin-1", "cp1252"}


def write_csv(dest: str | Path, rows: list[list[Any]], *, delimiter: str = ",",
              encoding: str = "utf-8", header: bool = True) -> dict[str, Any]:
    if not rows:
        raise ValueError("rows is empty: provide at least a header row")
    if encoding not in _ENCODINGS:
        raise ValueError(f"unsupported encoding {encoding!r} (use one of {sorted(_ENCODINGS)})")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = rows[1:] if not header else rows
    newline = ""
    with open(dest, "w", encoding=encoding, newline="") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        for row in body:
            w.writerow(["" if v is None else v for v in row])
    return {"bytes": dest.stat().st_size, "rows": len(body), "encoding": encoding, "delimiter": delimiter}


def read_csv(path: str | Path, max_rows: int = 100) -> dict[str, Any]:
    p = Path(path)
    raw = p.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = []
    truncated = False
    for i, row in enumerate(reader):
        if i >= max_rows:
            truncated = True
            break
        rows.append(row)
    return {"rows": rows, "truncated": truncated, "delimiter": delimiter, "encoding": encoding}


def markdown_table(rows: list[list[Any]], *, max_cols: int = 12, max_cell: int = 60) -> str:
    """Render rows as a GFM markdown table (for chat answers / documents)."""
    if not rows:
        return "(empty table)"
    rows = [list(r[:max_cols]) for r in rows]
    header = [str(v) for v in rows[0]]
    body = [[str(v) for v in r] for r in rows[1:]]

    def clean(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ")[:max_cell]

    widths = [len(clean(h)) for h in header]
    for r in body:
        for i, c in enumerate(r[: len(header)]):
            widths[i] = min(max(widths[i], len(clean(c))), max_cell)
    fmt = "| " + " | ".join(f"{{:{w}}}" for w in widths) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    out = [fmt.format(*[clean(h) for h in header]), sep]
    for r in body:
        padded = [clean(c) for c in r] + [""] * (len(header) - len(r))
        out.append(fmt.format(*padded[: len(header)]))
    return "\n".join(out)
