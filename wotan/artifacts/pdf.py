"""PDF writer built on ReportLab (the ``reportlab`` optional extra).

Accepts the shared markdown-lite source (see :mod:`.common`) and produces a
real PDF: styled headings, justified paragraphs with inline bold/italic/code,
bullet and numbered lists, styled tables, embedded images, quotes, code
blocks, page numbers and document metadata.

Fonts: prefers a Unicode TTF (DejaVu / Liberation / Arial / Calibri - Windows
and Linux both ship at least one) so Portuguese diacritics always render;
falls back to the PDF base fonts with latin-1 sanitization.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .common import Block, Inline, parse_markdown, sanitize_plain

_FONT_CANDIDATES = [
    # (regular, bold, italic) - per-weight fallback chains start here
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf"),
    (os.path.expandvars(r"%WINDIR%\Fonts\arial.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\arialbd.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\ariali.ttf")),
    (os.path.expandvars(r"%WINDIR%\Fonts\calibri.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\calibrib.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\calibrii.ttf")),
    ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Helvetica.ttc"),
]

_PAGE_SIZES = {"a4": "A4", "letter": "LETTER", "legal": "LEGAL"}


def _register_fonts() -> tuple[str, str, str, bool]:
    """Register a Unicode font trio (regular/bold/italic) with graceful
    degradation: each weight falls back to the best available file (a missing
    bold renders in the regular file rather than failing), and everything
    falls back to the PDF base fonts when no TTF exists."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    def first_existing(paths: list[str]) -> str:
        for p in paths:
            try:
                if p and os.path.isfile(p):
                    return p
            except OSError:
                continue
        return ""

    reg_path = first_existing([c[0] for c in _FONT_CANDIDATES])
    if not reg_path:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False
    bold_path = first_existing([c[1] for c in _FONT_CANDIDATES]) or reg_path
    ital_path = first_existing([c[2] for c in _FONT_CANDIDATES]) or reg_path
    try:
        pdfmetrics.registerFont(TTFont("WotanSans", reg_path))
        pdfmetrics.registerFont(TTFont("WotanSans-Bold", bold_path))
        pdfmetrics.registerFont(TTFont("WotanSans-Italic", ital_path))
        addMapping("WotanSans", 0, 0, "WotanSans")
        addMapping("WotanSans", 1, 0, "WotanSans-Bold")
        addMapping("WotanSans", 0, 1, "WotanSans-Italic")
        addMapping("WotanSans", 1, 1, "WotanSans-Bold")
        return "WotanSans", "WotanSans-Bold", "WotanSans-Italic", True
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False


def utils_color(hex_str: str):  # small local helper (imported lazily)
    from reportlab.lib import colors

    return colors.HexColor(hex_str)


def _spans_to_rich(inlines: list[Inline], unicode_ok: bool) -> str:
    """Render inline spans as reportlab mini-markup (text escaped)."""
    parts: list[str] = []
    for span in inlines:
        text = span.text
        if not unicode_ok:
            text = sanitize_plain(text)
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if span.code:
            text = f"<font face='Courier'>{text}</font>"
        if span.link:
            text = f"<u><font color='#1a56a0'>{text}</font></u>"
        if span.bold:
            text = f"<b>{text}</b>"
        if span.italic:
            text = f"<i>{text}</i>"
        parts.append(text)
    return "".join(parts)


def _table_for_reportlab(rows: list[list[str]], unicode_ok: bool, font_reg: str, font_bold: str) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    data = [[sanitize_plain(str(c)) if not unicode_ok else str(c) for c in row] for row in rows]
    t = Table(data, repeatRows=1, colWidths=None)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTNAME", (0, 1), (-1, -1), font_reg),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c2cc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _fit_col_widths(rows: list[list[str]], avail: float) -> list[float] | None:
    """Distribute width by content length, clamped and rescaled to fit."""
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    weights = []
    for c in range(ncols):
        longest = max((len(str(r[c])) if c < len(r) else 0 for r in rows), default=1)
        weights.append(max(4.0, min(float(longest), 60.0)))
    total = sum(weights)
    widths = [max(1.6 * 28.35, avail * w / total) for w in weights]
    scale = avail / sum(widths)
    return [w * scale for w in widths]


def _chunk_lines(lines: list[str], size: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    count = 0
    for line in lines:
        buf.append(line if line.strip() else " ")
        count += 1
        if count >= size:
            out.append("<br/>".join(buf))
            buf, count = [], 0
    if buf:
        out.append("<br/>".join(buf))
    return out or [" "]


def _escape_break(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _image_flow(block: Block, max_w: float, max_h: float):
    from pathlib import Path as _Path
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image as RLImage, Paragraph

    note_style = ParagraphStyle("imgnote", fontName="Helvetica", fontSize=8)

    path = _Path(block.path)
    if not path.is_file():
        return Paragraph(f"<font color='#b00020'>[image not found: {block.path}]</font>", note_style)
    try:
        img = RLImage(str(path))
    except Exception as exc:
        return Paragraph(f"<font color='#b00020'>[image error: {exc}]</font>", note_style)
    scale = min(max_w / img.drawWidth, max_h / img.drawHeight, 1.0)
    img.drawWidth *= scale
    img.drawHeight *= scale
    return img


def write_pdf(
    dest: str | Path,
    content_md: str,
    *,
    title: str = "",
    author: str = "",
    subject: str = "",
    page_size: str = "a4",
    page_numbers: bool = True,
    base_dir: Path | None = None,
    margin_cm: float = 2.0,
) -> dict[str, Any]:
    """Build the PDF from markdown-lite. Returns pages/bytes metadata."""
    try:
        from reportlab.lib.pagesizes import A4, LEGAL, LETTER
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, KeepTogether,
                                        PageBreak, PageTemplate, Paragraph, Spacer)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"reportlab is required to write PDFs ({exc})") from exc

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    reg, bold, ital, unicode_ok = _register_fonts()
    sizes = {"a4": A4, "letter": LETTER, "legal": LEGAL}
    page = sizes.get((page_size or "a4").lower(), A4)

    def footer(canvas, docobj) -> None:  # noqa: ANN001
        if not page_numbers:
            return
        canvas.saveState()
        canvas.setFont(reg, 8)
        canvas.setFillColor(utils_color("#8a97a5"))
        label = sanitize_plain(title) if title else dest.stem
        canvas.drawCentredString(page[0] / 2.0, 0.9 * cm, f"{label} - {canvas.getPageNumber()}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        str(dest),
        pagesize=page,
        leftMargin=margin_cm * cm, rightMargin=margin_cm * cm,
        topMargin=margin_cm * cm, bottomMargin=margin_cm * cm + (0.6 * cm if page_numbers else 0),
        title=title or dest.stem,
        author=author,
        subject=subject,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=footer)])

    body = ParagraphStyle("body", fontName=reg, fontSize=10.5, leading=15, spaceAfter=6, alignment=4)  # justify
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=20, leading=25, spaceBefore=10, spaceAfter=10, textColor=utils_color("#1f3a5f"))
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=15, leading=20, spaceBefore=12, spaceAfter=6, textColor=utils_color("#1f3a5f"))
    h3 = ParagraphStyle("h3", fontName=bold, fontSize=12, leading=16, spaceBefore=10, spaceAfter=4, textColor=utils_color("#33475b"))
    quote = ParagraphStyle("quote", fontName=ital, fontSize=10.5, leading=15, leftIndent=14, textColor=utils_color("#4a5a6a"), spaceAfter=6)
    code = ParagraphStyle("code", fontName="Courier", fontSize=8.5, leading=11.5, backColor=utils_color("#f2f4f7"), borderPadding=4, spaceAfter=6)
    bullet = ParagraphStyle("bullet", fontName=reg, fontSize=10.5, leading=15, leftIndent=14, bulletIndent=4, spaceAfter=2)

    story: list[Any] = []
    max_img_w = doc.width
    max_img_h = doc.height * 0.75

    for block in parse_markdown(content_md, base_dir=base_dir):
        kind = block.kind
        if kind == "heading":
            style = {1: h1, 2: h2, 3: h3}.get(block.level, h3)
            story.append(Paragraph(_spans_to_rich(block.inlines, unicode_ok), style))
        elif kind == "paragraph":
            story.append(Paragraph(_spans_to_rich(block.inlines, unicode_ok), body))
        elif kind == "quote":
            story.append(Paragraph(_spans_to_rich(block.inlines, unicode_ok), quote))
        elif kind == "code":
            lines = [line if unicode_ok else sanitize_plain(line) for line in block.text.splitlines()]
            for chunk in _chunk_lines(lines, 60):
                story.append(Paragraph(_escape_break(chunk), code))
        elif kind in ("bullet", "ordered"):
            for idx, item in enumerate(block.items):
                inl = block.item_inlines[idx] if idx < len(block.item_inlines) else []
                marker = "-" if kind == "bullet" else f"{idx + 1}."
                story.append(Paragraph(_spans_to_rich(inl, unicode_ok), bullet, bulletText=marker))
            story.append(Spacer(1, 4))
        elif kind == "table":
            tbl = _table_for_reportlab(block.rows, unicode_ok, font_reg=reg, font_bold=bold)
            tbl._argW = _fit_col_widths(block.rows, doc.width)
            story.append(Spacer(1, 2))
            story.append(tbl)
            story.append(Spacer(1, 8))
        elif kind == "image":
            flow = _image_flow(block, max_img_w, max_img_h)
            story.append(KeepTogether([Spacer(1, 4), flow, Spacer(1, 8)]))
        elif kind == "rule":
            story.append(HRFlowable(width="100%", thickness=0.6, color=utils_color("#b8c2cc"), spaceAfter=8))
        elif kind == "pagebreak":
            story.append(PageBreak())

    if not story:
        story.append(Paragraph("", body))
    doc.build(story)
    pages = 0
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(str(dest)).pages)
    except Exception:
        pass
    return {"pages": pages, "bytes": dest.stat().st_size, "font_unicode": unicode_ok}
