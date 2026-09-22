"""PowerPoint (.pptx) writer built on python-pptx (the ``python-pptx`` extra).

Design philosophy: SOBER and MODERN. Flat muted color themes, generous
whitespace, thin accent rules, restrained typography - explicitly NOT the
glossy neon-gradient style that generative tools tend to produce. Themes are
curated (executive, nordic, editorial, graphite, terra); people imagery is
expected to come from photorealistic LLM generation (see ``img_llm``), never
from clipart.

Slides are described as data (no template lookup, so PowerPoint, LibreOffice
and Google Slides all open them). Layouts: title, section, agenda, bullets,
two_content, kpi, chart, table, timeline, image (full | right | full-bleed
variants), quote. Speaker notes supported. Reads decks back (read_pptx).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
EMU = 914400

# Curated, muted themes. No neon, no gradients, no glossy fills.
THEMES: dict[str, dict[str, str]] = {
    "executive": {
        "bg": "FFFFFF", "title": "1F2A37", "text": "333B45", "muted": "6E7783",
        "accent": "1F3A5F", "accent2": "5B6B7A", "rule": "D8DDE3",
        "card_fill": "F3F5F8", "table_alt": "F5F7F9",
        "title_font": "Calibri", "body_font": "Calibri",
    },
    "nordic": {
        "bg": "FAFBFC", "title": "1C2B33", "text": "31404A", "muted": "647480",
        "accent": "33606D", "accent2": "7C97A1", "rule": "DCE3E6",
        "card_fill": "EFF3F4", "table_alt": "F4F7F8",
        "title_font": "Calibri", "body_font": "Calibri",
    },
    "editorial": {
        "bg": "FDFCF8", "title": "26221C", "text": "3A342C", "muted": "7A7266",
        "accent": "743C3C", "accent2": "9A8F7F", "rule": "E2DDD2",
        "card_fill": "F6F3EC", "table_alt": "F8F5EF",
        "title_font": "Georgia", "body_font": "Calibri",
    },
    "graphite": {
        "bg": "FFFFFF", "title": "141414", "text": "2E2E2E", "muted": "767676",
        "accent": "3D3D3D", "accent2": "8C8C8C", "rule": "DBDBDB",
        "card_fill": "F4F4F4", "table_alt": "F7F7F7",
        "title_font": "Arial", "body_font": "Arial",
    },
    "terra": {
        "bg": "FBFAF7", "title": "2B2620", "text": "413A31", "muted": "7D7466",
        "accent": "5E6B4F", "accent2": "A4917A", "rule": "E4E0D6",
        "card_fill": "F3F1E9", "table_alt": "F7F5EF",
        "title_font": "Calibri", "body_font": "Calibri",
    },
}


def _hex(color: str | tuple) -> str:
    if isinstance(color, tuple):
        return "".join(f"{c:02X}" for c in color)
    return str(color).lstrip("#").upper()[:6]


def _theme(name: str) -> dict[str, str]:
    return THEMES.get(str(name or "executive").lower(), THEMES["executive"])


class _Deck:
    """Small drawing toolkit over python-pptx with theme-aware defaults."""

    def __init__(self, prs, theme: dict[str, str]) -> None:
        self.prs = prs
        self.t = theme
        self.W = prs.slide_width
        self.H = prs.slide_height
        self.blank = prs.slide_layouts[6]

    # -- primitives ---------------------------------------------------------
    def slide(self, bg: str | None = None):
        s = self.prs.slides.add_slide(self.blank)
        fill = s.background.fill
        fill.solid()
        from pptx.dml.color import RGBColor

        fill.fore_color.rgb = RGBColor.from_string(_hex(bg or self.t["bg"]))
        return s

    def rect(self, s, x_in, y_in, w_in, h_in, fill: str | None = None, line: str | None = None, line_w: float = 0.75):
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt

        shp = s.shapes.add_shape(1, Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = RGBColor.from_string(_hex(fill))
        else:
            shp.fill.background()
        if line:
            shp.line.color.rgb = RGBColor.from_string(_hex(line))
            shp.line.width = Pt(line_w)
        else:
            shp.line.fill.background()
        shp.shadow.inherit = False
        return shp

    def text(self, s, x_in, y_in, w_in, h_in, content, *, size=18, bold=False, italic=False,
             color=None, align="left", font=None, line_spacing=1.0, space_after=0, wrap=True,
             anchor="top"):
        from pptx.dml.color import RGBColor
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Inches, Pt

        box = s.shapes.add_textbox(Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
        tf = box.text_frame
        tf.word_wrap = wrap
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}[anchor]
        lines = content.split("\n") if content else [""]
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}[align]
            if line_spacing != 1.0:
                p.line_spacing = line_spacing
            if space_after:
                p.space_after = Pt(space_after)
            run = p.add_run()
            run.text = line
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.italic = italic
            run.font.name = font or self.t["body_font"]
            run.font.color.rgb = RGBColor.from_string(_hex(color or self.t["text"]))
        return box

    def bullets(self, s, x_in, y_in, w_in, h_in, items: list, *, size=17, color=None,
                marker_color=None, line_spacing=1.12, space_after=8):
        """Dash-marker bullets (sober) with nested level support ('> ' prefix)."""
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt

        box = s.shapes.add_textbox(Inches(x_in), Inches(y_in), Inches(w_in), Inches(h_in))
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        first = True
        for item in items:
            level = 0
            text = str(item)
            if text.startswith("> "):
                level = 1
                text = text[2:]
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.line_spacing = line_spacing
            p.space_after = Pt(space_after)
            p.level = min(level, 3)
            run = p.add_run()
            run.text = ("–  " if level == 0 else "·  ") + text
            run.font.size = Pt(size - level * 2)
            run.font.name = self.t["body_font"]
            run.font.color.rgb = RGBColor.from_string(_hex(color or self.t["text"]))
        return box

    def accent_rule(self, s, x_in, y_in, w_in=0.9, color=None, weight=2.6):
        """The thin accent line under titles - the one decorative element."""
        from pptx.util import Inches, Pt

        line = s.shapes.add_connector(1, Inches(x_in), Inches(y_in), Inches(x_in + w_in), Inches(y_in))
        from pptx.dml.color import RGBColor

        line.line.color.rgb = RGBColor.from_string(_hex(color or self.t["accent"]))
        line.line.width = Pt(weight)
        return line

    def picture(self, s, image_path: str, x_in, y_in, w_in, h_in) -> float:
        """Fit-center an image into the box; returns the drawn height (in)."""
        from PIL import Image
        from pptx.util import Inches

        with Image.open(image_path) as im:
            iw, ih = im.size
        box_ratio = w_in / max(h_in, 0.01)
        img_ratio = iw / max(ih, 1)
        if img_ratio >= box_ratio:
            draw_w = w_in
            draw_h = w_in / img_ratio
        else:
            draw_h = h_in
            draw_w = h_in * img_ratio
        x = x_in + (w_in - draw_w) / 2
        y = y_in + (h_in - draw_h) / 2
        s.shapes.add_picture(str(image_path), Inches(x), Inches(y), Inches(draw_w), Inches(draw_h))
        return draw_h

    def footer(self, s, deck_title: str, page_no: int | None) -> None:
        if deck_title:
            self.text(s, 0.62, self.H / EMU - 0.42, 6.5, 0.3, deck_title, size=9, color=self.t["muted"])
        if page_no is not None:
            self.text(s, self.W / EMU - 1.1, self.H / EMU - 0.42, 0.5, 0.3, str(page_no), size=9,
                      color=self.t["muted"], align="right")

    def header(self, s, title: str, *, kicker: str = ""):
        y = 0.52
        if kicker:
            self.text(s, 0.62, y, 9.5, 0.3, kicker.upper(), size=11, bold=True, color=self.t["accent2"], font=self.t["body_font"])
            y += 0.38
        self.text(s, 0.62, y, self.W / EMU - 1.24, 0.75, title, size=27, bold=True,
                  color=self.t["title"], font=self.t["title_font"])
        self.accent_rule(s, 0.64, y + (0.62 if len(title) < 60 else 1.18))
        return y + 1.05


def _norm_bullets(value) -> list[str]:
    if not value:
        return []
    return [str(v) for v in value]


def write_pptx(dest: str | Path, slides: list[dict[str, Any]], *, title: str = "",
               author: str = "", aspect: str = "16:9", theme: str = "executive",
               footer_title: str = "") -> dict[str, Any]:
    """Build the deck. ``slides`` entries carry {layout, ...}; see THEMES and
    the tool schema for the layout catalogue."""
    try:
        import pptx
        from pptx.util import Inches
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"python-pptx is required to write .pptx files ({exc})") from exc

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    prs = pptx.Presentation()
    if str(aspect) == "4:3":
        prs.slide_width = Inches(10)
        prs.slide_height = Inches(7.5)
    else:
        prs.slide_width = Inches(SLIDE_W_IN)
        prs.slide_height = Inches(SLIDE_H_IN)
    deck = _Deck(prs, _theme(theme))
    W_IN, H_IN = prs.slide_width / EMU, prs.slide_height / EMU
    MARGIN = 0.62
    CONTENT_W = W_IN - 2 * MARGIN

    def resolve_image(spec_key: str, spec: dict) -> str:
        p = Path(str(spec.get(spec_key, "")))
        if not p.is_absolute() and not p.is_file():
            candidate = Path(dest).parent / p
            if candidate.is_file():
                return str(candidate)
        if not p.is_file():
            raise FileNotFoundError(str(p))
        return str(p)

    def notes(s, spec) -> None:  # noqa: ANN001
        if spec.get("notes"):
            s.notes_slide.notes_text_frame.text = str(spec["notes"])

    n = len(slides)
    for idx, spec in enumerate(slides):
        if not isinstance(spec, dict):
            raise ValueError(f"slide {idx + 1} must be an object with a 'layout'")
        layout = str(spec.get("layout", "bullets")).lower()
        page_no = idx + 1 if not (layout in ("title", "section") or (layout == "quote" and not spec.get("page_number"))) else None
        s = deck.slide(spec.get("bg"))

        if layout == "title":
            mid_y = H_IN * 0.36
            deck.text(s, MARGIN + 0.05, mid_y, CONTENT_W - 0.5, 1.7,
                      str(spec.get("title", "")), size=44, bold=True,
                      color=deck.t["title"], font=deck.t["title_font"])
            deck.accent_rule(s, MARGIN + 0.07, mid_y + 1.28, w_in=1.15, weight=3.0)
            if spec.get("subtitle"):
                deck.text(s, MARGIN + 0.06, mid_y + 1.55, CONTENT_W - 0.6, 1.0,
                          str(spec["subtitle"]), size=19, color=deck.t["muted"])
            meta = "  |  ".join([str(spec[k]) for k in ("author", "date") if spec.get(k)])
            if meta:
                deck.text(s, MARGIN + 0.06, H_IN - 0.95, CONTENT_W, 0.4, meta, size=12, color=deck.t["muted"])

        elif layout == "section":
            deck.rect(s, 0, 0, 0.28, H_IN, fill=deck.t["accent"])
            num = str(spec.get("number", idx + 1))
            deck.text(s, MARGIN + 0.15, H_IN * 0.34, 1.6, 1.0, f"{int(num):02d}" if num.isdigit() else num,
                      size=54, bold=True, color=deck.t["rule"], font=deck.t["title_font"])
            deck.text(s, MARGIN + 1.35, H_IN * 0.38, CONTENT_W - 1.6, 1.2,
                      str(spec.get("title", "")), size=34, bold=True,
                      color=deck.t["title"], font=deck.t["title_font"])
            if spec.get("subtitle"):
                deck.text(s, MARGIN + 1.37, H_IN * 0.38 + 1.0, CONTENT_W - 2.0, 0.8,
                          str(spec["subtitle"]), size=15, color=deck.t["muted"])

        elif layout == "agenda":
            body_top = deck.header(s, str(spec.get("title", "Agenda")), kicker=str(spec.get("kicker", "")))
            items = _norm_bullets(spec.get("items") or spec.get("bullets"))
            col_split = min(max(len(items) // 2, 3), 6) if len(items) > 6 else len(items)
            rows = items[:col_split]
            for i, item in enumerate(rows, 1):
                deck.text(s, MARGIN, body_top + (i - 1) * 0.62, 0.55, 0.5, f"{i:02d}",
                          size=17, bold=True, color=deck.t["accent2"])
                deck.text(s, MARGIN + 0.62, body_top + (i - 1) * 0.62, CONTENT_W * 0.52, 0.55,
                          item, size=16, color=deck.t["text"])
            for j, item in enumerate(items[col_split:], 1):
                r = col_split + j
                deck.text(s, W_IN / 2 + 0.15, body_top + (j - 1) * 0.62, 0.55, 0.5, f"{r:02d}",
                          size=17, bold=True, color=deck.t["accent2"])
                deck.text(s, W_IN / 2 + 0.77, body_top + (j - 1) * 0.62, CONTENT_W / 2 - 0.6, 0.55,
                          item, size=16, color=deck.t["text"])

        elif layout in ("kpi", "cards"):
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            cards = list(spec.get("cards") or [])
            if not cards:
                raise ValueError("kpi layout needs cards: [{value, label}]")
            n_cards = min(len(cards), 4)
            gap = 0.3
            card_w = (CONTENT_W - gap * (n_cards - 1)) / n_cards
            card_h = float(spec.get("card_height", 2.0))
            for i, card in enumerate(cards[:4]):
                x = MARGIN + i * (card_w + gap)
                deck.rect(s, x, body_top + 0.15, card_w, card_h, fill=deck.t["card_fill"])
                deck.rect(s, x, body_top + 0.15, card_w, 0.045, fill=deck.t["accent"])
                deck.text(s, x + 0.22, body_top + 0.42, card_w - 0.44, 0.85,
                          str(card.get("value", "")), size=int(card.get("value_size", 34)), bold=True,
                          color=deck.t["accent"], font=deck.t["title_font"])
                deck.text(s, x + 0.22, body_top + 1.32, card_w - 0.44, card_h - 1.45,
                          str(card.get("label", "")), size=13, color=deck.t["muted"])
            if spec.get("takeaway"):
                deck.text(s, MARGIN, body_top + card_h + 0.45, CONTENT_W, 0.6,
                          str(spec["takeaway"]), size=15, italic=True, color=deck.t["text"])

        elif layout == "chart":
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            img = resolve_image("image_path", spec)
            takeaway_h = 0.62 if spec.get("takeaway") else 0.0
            deck.picture(s, img, MARGIN, body_top + 0.1, CONTENT_W, H_IN - body_top - 0.55 - takeaway_h)
            if spec.get("takeaway"):
                deck.text(s, MARGIN, H_IN - 1.05, CONTENT_W, 0.55, str(spec["takeaway"]),
                          size=14, italic=True, color=deck.t["muted"])

        elif layout == "table":
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            from pptx.util import Inches, Pt

            rows = spec.get("rows") or []
            if not rows:
                raise ValueError("table layout needs rows: [[...], [...]]")
            ncols = max(len(r) for r in rows)
            nrows = len(rows)
            tbl_w = CONTENT_W
            tbl_h = min(H_IN - body_top - 0.7, 0.52 * nrows)
            shape = s.shapes.add_table(nrows, ncols, Inches(MARGIN), Inches(body_top + 0.12),
                                       Inches(tbl_w), Inches(tbl_h))
            table = shape.table
            widths = spec.get("col_widths") or []
            if widths and len(widths) == ncols:
                total = sum(float(w) for w in widths)
                for ci, w in enumerate(widths):
                    table.columns[ci].width = Inches(tbl_w * float(w) / total)
            for ri, row in enumerate(rows[:nrows]):
                for ci in range(ncols):
                    cell = table.cell(ri, ci)
                    cell_value = str(row[ci]) if ci < len(row) else ""
                    cell.text = cell_value
                    para = cell.text_frame.paragraphs[0]
                    run = para.runs[0] if para.runs else para.add_run()
                    run.font.size = Pt(13 if ri else 12.5)
                    run.font.name = deck.t["body_font"]
                    from pptx.dml.color import RGBColor

                    if ri == 0:
                        run.font.bold = True
                        run.font.color.rgb = RGBColor.from_string("FFFFFF")
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = RGBColor.from_string(_hex(deck.t["accent"]))
                    else:
                        run.font.color.rgb = RGBColor.from_string(_hex(deck.t["text"]))
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = RGBColor.from_string(
                            _hex(deck.t["table_alt"] if ri % 2 == 0 else "FFFFFF"))

        elif layout == "timeline":
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            from pptx.util import Inches, Pt

            steps = list(spec.get("steps") or [])
            if len(steps) < 2:
                raise ValueError("timeline needs at least 2 steps: [{label, sub}]")
            line_y = body_top + 1.1
            deck.accent_rule(s, MARGIN + 0.2, line_y, w_in=CONTENT_W - 0.4,
                             color=deck.t["rule"], weight=2.2)
            slot = (CONTENT_W - 0.4) / (len(steps) - 1)
            for i, step in enumerate(steps):
                cx = MARGIN + 0.2 + i * slot
                dot = s.shapes.add_shape(9, Inches(cx - 0.07), Inches(line_y - 0.07), Inches(0.14), Inches(0.14))
                from pptx.dml.color import RGBColor

                dot.fill.solid()
                dot.fill.fore_color.rgb = RGBColor.from_string(_hex(deck.t["accent"]))
                dot.line.fill.background()
                dot.shadow.inherit = False
                above = i % 2 == 0
                ty = line_y - 0.78 if above else line_y + 0.28
                deck.text(s, cx - 1.0, ty, 2.0, 0.5, str(step.get("label", "")), size=14, bold=True,
                          color=deck.t["title"], align="center")
                if step.get("sub"):
                    deck.text(s, cx - 1.0, ty + 0.34, 2.0, 0.6, str(step["sub"]), size=11,
                              color=deck.t["muted"], align="center")

        elif layout == "quote":
            deck.rect(s, 0, 0, 0.28, H_IN, fill=deck.t["accent2"])
            quote = str(spec.get("text", spec.get("title", "")))
            deck.text(s, 1.2, H_IN * 0.30, W_IN - 2.6, 1.9, '"' + quote + '"',
                      size=30, italic=True, color=deck.t["title"], font=deck.t["title_font"])
            if spec.get("author"):
                deck.text(s, 1.22, H_IN * 0.30 + 1.75, W_IN - 2.6, 0.5, "- " + str(spec["author"]),
                          size=15, color=deck.t["muted"])

        elif layout in ("image", "image_full"):
            img = resolve_image("image_path", spec)
            if spec.get("title") or spec.get("kicker"):
                body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
                caption = str(spec.get("caption", ""))
                cap_h = 0.4 if caption else 0.0
                deck.picture(s, img, MARGIN, body_top + 0.1, CONTENT_W, H_IN - body_top - 0.5 - cap_h)
                if caption:
                    deck.text(s, MARGIN, H_IN - 0.82, CONTENT_W, 0.35, caption, size=11, color=deck.t["muted"])
            else:
                deck.picture(s, img, 0.0, 0.0, W_IN, H_IN)
                if spec.get("caption"):
                    bar_h = 0.52
                    deck.rect(s, 0, H_IN - bar_h, W_IN, bar_h, fill="FFFFFF")
                    deck.text(s, MARGIN, H_IN - bar_h + 0.12, CONTENT_W, 0.3,
                              str(spec["caption"]), size=11, color=deck.t["muted"])

        elif layout == "two_content":
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            half = (CONTENT_W - 0.7) / 2
            left_head = str(spec.get("left_title", ""))
            right_head = str(spec.get("right_title", ""))
            y_l = body_top
            if left_head:
                deck.text(s, MARGIN, body_top, half, 0.4, left_head, size=15, bold=True, color=deck.t["accent"])
                y_l += 0.5
            deck.bullets(s, MARGIN, y_l, half, H_IN - y_l - 0.8, _norm_bullets(spec.get("left")), size=15)
            x_r = MARGIN + half + 0.7
            y_r = body_top
            if right_head:
                deck.text(s, x_r, body_top, half, 0.4, right_head, size=15, bold=True, color=deck.t["accent"])
                y_r += 0.5
            deck.bullets(s, x_r, y_r, half, H_IN - y_r - 0.8, _norm_bullets(spec.get("right")), size=15)
            img = spec.get("image_path")
            if img:
                deck.picture(s, resolve_image("image_path", spec), x_r, y_r, half, H_IN - y_r - 0.8)

        else:  # bullets (default), optional image on the right
            body_top = deck.header(s, str(spec.get("title", "")), kicker=str(spec.get("kicker", "")))
            items = _norm_bullets(spec.get("bullets"))
            img = spec.get("image_path")
            if img:
                img_w = CONTENT_W * 0.44
                img_h = H_IN - body_top - 0.75
                deck.picture(s, resolve_image("image_path", spec), W_IN - MARGIN - img_w, body_top + 0.05,
                             img_w, img_h)
                text_w = CONTENT_W - img_w - 0.55
            else:
                text_w = CONTENT_W
            size = 18 if len(items) <= 6 else (15.5 if len(items) <= 9 else 13.5)
            if items:
                deck.bullets(s, MARGIN, body_top + 0.12, text_w, H_IN - body_top - 0.9, items, size=size)
            if spec.get("takeaway"):
                deck.text(s, MARGIN, H_IN - 1.02, text_w, 0.55, str(spec["takeaway"]),
                          size=14, italic=True, color=deck.t["muted"])

        notes(s, spec)
        if page_no is not None and spec.get("page_number", True):
            deck.footer(s, str(spec.get("footer_title", footer_title)), page_no)

    prs.core_properties.title = title or dest.stem
    if author:
        prs.core_properties.author = author
    prs.save(str(dest))
    return {"bytes": dest.stat().st_size, "slides": len(slides), "theme": theme}


def read_pptx(path: str | Path, max_chars: int = 6000) -> dict[str, Any]:
    from pptx import Presentation

    prs = Presentation(str(path))
    out = []
    total = 0
    for idx, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                if txt:
                    texts.append(txt)
        entry = {"slide": idx, "text": " | ".join(texts)[:1200]}
        total += len(entry["text"])
        out.append(entry)
    return {"slides": out, "slide_count": len(out), "truncated": total > max_chars}
