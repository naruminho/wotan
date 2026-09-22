"""Markdown-lite: one small parser shared by every document writer.

Supported blocks (a practical subset of CommonMark + GFM):

- headings: ``#`` / ``##`` / ``###`` (deeper levels are rendered as bold text)
- bullet lists: ``- item`` (nesting with two spaces); ordered lists ``1. item``
- fenced code blocks: ``` ... ```
- quotes: ``> text``
- GFM tables: a row of ``|...|`` followed by a ``|---|---|`` separator row
- images: ``![alt](relative/or/absolute/path.png)`` as its own paragraph
- page break: a line containing only ``<<<PAGEBREAK>>>``
- horizontal rule: ``---``
- paragraphs (blank line separated)

Inline styles: ``**bold**``, ``*italic*``, ```` `code` ````, ``[text](url)``.
A literal ``\\*`` escapes the following marker. The parser produces plain data
(no XML), so each writer can map it to its own markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PAGEBREAK_MARKER = "<<<PAGEBREAK>>>"


@dataclass
class Inline:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: str = ""  # href when the span is a link


@dataclass
class Block:
    kind: str  # heading | paragraph | bullet | ordered | code | quote | table | image | rule | pagebreak
    level: int = 0  # heading level (1-3)
    text: str = ""  # paragraph/heading/quote/code text (code keeps raw)
    items: list[str] = field(default_factory=list)  # bullet/ordered items
    rows: list[list[str]] = field(default_factory=list)  # table rows (first = header)
    alt: str = ""  # image alt text
    path: str = ""  # image path
    lang: str = ""  # fenced code language
    inlines: list[Inline] = field(default_factory=list)  # paragraph/heading spans
    item_inlines: list[list[Inline]] = field(default_factory=list)  # list item spans


_INLINE_TOKEN = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<bold>\*\*(?P<bold_body>.+?)\*\*)"
    r"|(?P<italic>(?<!\*)\*(?![*\s])(?P<italic_body>[^*\n]+?)\*(?!\*))"
    r"|(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\))"
    r"|(?P<esc>\\[*`\\])",
    re.DOTALL,
)


def parse_inline(text: str) -> list[Inline]:
    """Split a line into styled spans. Unmatched markers stay literal."""
    out: list[Inline] = []
    pos = 0
    for m in _INLINE_TOKEN.finditer(text):
        if m.start() > pos:
            out.append(Inline(text=text[pos:m.start()]))
        if m.group("code"):
            out.append(Inline(text=m.group("code")[1:-1], code=True))
        elif m.group("bold"):
            body = m.group("bold_body")
            inner = parse_inline(body)
            if len(inner) == 1 and not inner[0].bold:
                out.append(Inline(text=inner[0].text, bold=True, italic=inner[0].italic, link=inner[0].link))
            else:
                for span in inner:
                    span.bold = True
                    out.append(span)
        elif m.group("italic"):
            body = m.group("italic_body")
            inner = parse_inline(body)
            if len(inner) == 1 and not inner[0].italic:
                out.append(Inline(text=inner[0].text, italic=True, bold=inner[0].bold, link=inner[0].link))
            else:
                for span in inner:
                    span.italic = True
                    out.append(span)
        elif m.group("link"):
            out.append(Inline(text=m.group("link_text"), link=m.group("link_url")))
        elif m.group("esc"):
            out.append(Inline(text=m.group("esc")[1]))
        pos = m.end()
    if pos < len(text):
        out.append(Inline(text=text[pos:]))
    return out or [Inline(text="")]


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_markdown(source: str, base_dir: Path | None = None) -> list[Block]:
    """Parse the markdown-lite source into blocks. Never raises on content."""
    blocks: list[Block] = []
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = " ".join(s.strip() for s in para).strip()
            if text:
                blocks.append(Block(kind="paragraph", text=text, inlines=parse_inline(text)))
            para.clear()

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        if line.strip().startswith("```"):
            flush_para()
            lang = line.strip()[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            blocks.append(Block(kind="code", text="\n".join(code_lines), lang=lang))
            continue

        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue

        if stripped == PAGEBREAK_MARKER:
            flush_para()
            blocks.append(Block(kind="pagebreak"))
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para()
            level = min(len(m.group(1)), 3)
            text = m.group(2).strip()
            blocks.append(Block(kind="heading", level=level, text=text, inlines=parse_inline(text)))
            i += 1
            continue

        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            flush_para()
            blocks.append(Block(kind="rule"))
            i += 1
            continue

        # table: current line has pipes and the next line is a |---| separator
        if "|" in stripped and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1] or ""):
            header = _split_row(stripped)
            sep = lines[i + 1]
            if set(sep.replace("|", "").replace("-", "").replace(":", "").replace(" ", "")) == set():
                rows = [header]
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    rows.append(_split_row(lines[i].strip()))
                    i += 1
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                blocks.append(Block(kind="table", rows=rows))
                continue

        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", stripped)
        if m:
            flush_para()
            path = m.group(2)
            if base_dir is not None and not Path(path).is_absolute():
                path = str(base_dir / path)
            blocks.append(Block(kind="image", alt=m.group(1), path=path))
            i += 1
            continue

        m = re.match(r"^(\s*)-\s+(.*)$", raw)
        if m:
            flush_para()
            indent = len(m.group(1))
            items: list[str] = []
            item_inlines: list[list[Inline]] = []
            while True:
                mm = re.match(r"^(\s*)-\s+(.*)$", lines[i].rstrip())
                if not mm:
                    break
                # nested items are flattened with a two-space prefix marker
                prefix = "> " if len(mm.group(1)) >= 2 and len(mm.group(1)) > indent else ""
                items.append(prefix + mm.group(2).strip())
                item_inlines.append(parse_inline(mm.group(2).strip()))
                i += 1
            blocks.append(Block(kind="bullet", items=items, item_inlines=item_inlines))
            continue

        m = re.match(r"^\s*\d+[.)]\s+(.*)$", raw)
        if m:
            flush_para()
            items = []
            item_inlines = []
            while True:
                mm = re.match(r"^\s*\d+[.)]\s+(.*)$", lines[i].rstrip())
                if not mm:
                    break
                items.append(mm.group(1).strip())
                item_inlines.append(parse_inline(mm.group(1).strip()))
                i += 1
            blocks.append(Block(kind="ordered", items=items, item_inlines=item_inlines))
            continue

        if stripped.startswith(">"):
            flush_para()
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            text = " ".join(q for q in quote_lines if q).strip()
            blocks.append(Block(kind="quote", text=text, inlines=parse_inline(text)))
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return blocks


def sanitize_plain(text: str) -> str:
    """Replace typographic Unicode with ASCII equivalents (Windows-safe)."""
    table = {
        "\u2013": "-", "\u2014": " - ", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
        "\u2022": "-", "\u2192": "->", "\u2705": "[OK]", "\u2713": "[OK]",
        "\u274c": "[ERROR]", "\u26a0": "[WARN]",
    }
    for k, v in table.items():
        text = text.replace(k, v)
    return text


def latexish_sanitize(text: str) -> str:
    """Neutralize characters that break XML-based writers (& < >)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def guess_image_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "bmp": "bmp", "tif": "tiff", "tiff": "tiff"}.get(suffix, "png")


def rows_from_value(value: Any) -> list[list[Any]]:
    """Coerce a tool argument into a 2D table (list of lists) or raise ValueError."""
    if isinstance(value, dict):
        # {"header": [cols], "rows": [[...]]} convenience shape
        if "rows" in value:
            header = value.get("header") or []
            rows = value.get("rows") or []
            if rows and isinstance(rows[0], dict):
                cols = list(rows[0].keys())
                return [header or cols] + [[r.get(c) for c in cols] for r in rows]
            return [list(header)] + [list(r) for r in rows]
        # {"col": [values], ...} column-oriented
        cols = list(value.keys())
        length = max((len(v) for v in value.values() if isinstance(v, list)), default=0)
        return [cols] + [[value[c][i] if isinstance(value.get(c), list) and i < len(value[c]) else "" for c in cols] for i in range(length)]
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            cols = list(value[0].keys())
            return [cols] + [[r.get(c) for c in cols] for r in value]
        return [list(r) for r in value]
    raise ValueError("expected a 2D array, a list of objects, a {header, rows} object or a {col: [values]} object")
