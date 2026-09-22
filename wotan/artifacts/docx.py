"""Word (.docx) writer built on python-docx (the ``python-docx`` extra).

Consumes the same markdown-lite source as the PDF writer: styled headings,
paragraphs with inline bold/italic/code/hyperlinks, lists, tables, images,
quotes and page breaks. Page size/margins are the Word defaults (A4 in most
locales); the result opens in Word, LibreOffice and Google Docs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import Block, Inline, parse_markdown, sanitize_plain


def _add_hyperlink(paragraph, url: str, text: str) -> None:  # noqa: ANN001
    """python-docx has no high-level hyperlink API; use the low-level one."""
    import docx.opc.constants
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement

    part = paragraph.part
    r_id = part.relate_to(url, docx.opc.constants.RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "1A56A0")
    rPr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rPr.append(underline)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _spans(paragraph, inlines: list[Inline]) -> None:  # noqa: ANN001
    for span in inlines:
        text = span.text
        if not text:
            continue
        if span.link:
            _add_hyperlink(paragraph, span.link, sanitize_plain(text))
            continue
        run = paragraph.add_run(sanitize_plain(text))
        if span.bold:
            run.bold = True
        if span.italic:
            run.italic = True
        if span.code:
            run.font.name = "Consolas"
            run.font.size = None  # keep document default size
            from docx.shared import Pt

            run.font.size = Pt(9.5)


def _add_table(doc, rows: list[list[str]]) -> None:  # noqa: ANN001
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml.shared import OxmlElement
    from docx.shared import Pt, RGBColor

    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for c_idx in range(ncols):
            text = sanitize_plain(str(row[c_idx])) if c_idx < len(row) else ""
            cell = cells[c_idx]
            cell.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(text)
            run.font.size = Pt(9)
            if r_idx == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
                # light header shading
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "EEF2F7")
                cell._tc.get_or_add_tcPr().append(shd)
    doc.add_paragraph()


def write_docx(
    dest: str | Path,
    content_md: str,
    *,
    title: str = "",
    author: str = "",
    subject: str = "",
    base_dir: Path | None = None,
) -> dict[str, Any]:
    try:
        import docx
        from docx.enum.text import WD_BREAK, WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Inches, Pt, RGBColor
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"python-docx is required to write .docx files ({exc})") from exc

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    document = docx.Document()
    core = document.core_properties
    core.title = title or Path(dest).stem
    if author:
        core.author = author
    if subject:
        core.subject = subject

    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    for block in parse_markdown(content_md, base_dir=base_dir):
        kind = block.kind
        if kind == "heading":
            level = max(1, min(block.level, 3))
            h = document.add_heading("", level=level)
            _spans(h, block.inlines)
            for run in h.runs:
                run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        elif kind == "paragraph":
            p = document.add_paragraph()
            if block.inlines and block.inlines[0].text.startswith("[center] "):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                block.inlines[0].text = block.inlines[0].text[len("[center] "):]
            _spans(p, block.inlines)
        elif kind == "quote":
            p = document.add_paragraph(style="Intense Quote")
            _spans(p, block.inlines)
        elif kind == "code":
            p = document.add_paragraph()
            run = p.add_run(block.text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif kind == "bullet":
            for item in block.items:
                p = document.add_paragraph(style="List Bullet" if not item.startswith("> ") else "List Bullet 2")
                _spans(p, parse_markdown_item(item))
        elif kind == "ordered":
            for item in block.items:
                p = document.add_paragraph(style="List Number")
                _spans(p, parse_markdown_item(item))
        elif kind == "table":
            _add_table(document, block.rows)
        elif kind == "image":
            path = Path(block.path)
            if path.is_file():
                try:
                    document.add_picture(str(path), width=Inches(5.8))
                    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    document.add_paragraph(f"[image error: {path.name}]")
            else:
                document.add_paragraph(f"[image not found: {block.path}]")
        elif kind == "rule":
            p = document.add_paragraph()
            run = p.add_run("_" * 64)
            run.font.color.rgb = RGBColor(0xB8, 0xC2, 0xCC)
        elif kind == "pagebreak":
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    document.save(str(dest))
    return {"bytes": dest.stat().st_size}


def parse_markdown_item(item: str) -> list[Inline]:
    from .common import parse_inline

    if item.startswith("> "):
        item = item[2:]
    return parse_inline(item)
