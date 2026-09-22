"""Synthetic scanned/photographed documents for OCR experiments.

Renders a document (markdown-lite, or a structured form spec) onto paper and
then degrades it the way a scanner or a phone camera would: small rotation,
paper tint, brightness gradient/shadow, sensor noise, slight blur, harsh JPEG
re-compression and (in photo mode) perspective skew + vignette. Fully seeded
and deterministic - the same seed produces the same scan, so OCR test sets are
reproducible.

Outputs PNG (single page) or a real scanned-style PDF (image-backed pages,
exactly what OCR pipelines ingest). No extra dependencies beyond Pillow and
the already-optional reportlab.
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Any

from .common import PAGEBREAK_MARKER, parse_inline, parse_markdown, sanitize_plain

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def _font_paths() -> dict[str, str]:
    import os

    candidates: dict[str, list[str]] = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            os.path.expandvars(r"%WINDIR%\Fonts\arial.ttf"),
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            os.path.expandvars(r"%WINDIR%\Fonts\arialbd.ttf"),
        ],
        "italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
            os.path.expandvars(r"%WINDIR%\Fonts\ariali.ttf"),
        ],
        "mono": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            os.path.expandvars(r"%WINDIR%\Fonts\consola.ttf"),
        ],
    }
    found: dict[str, str] = {}
    for key, paths in candidates.items():
        for p in paths:
            try:
                if p and os.path.isfile(p):
                    found[key] = p
                    break
            except OSError:
                continue
    # degrade: missing variants fall back to whatever exists
    fallback = next(iter(found.values()), "")
    for key in ("regular", "bold", "italic", "mono"):
        found.setdefault(key, fallback)
    return found


def _fonts(scale: float) -> dict[str, Any]:
    from PIL import ImageFont

    paths = _font_paths()
    sizes = {
        "h1": (30, "bold"), "h2": (24, "bold"), "h3": (20, "bold"),
        "body": (18, "regular"), "small": (14, "regular"),
        "field_label": (17, "regular"), "field_value": (19, "italic"),
        "table": (15, "regular"), "table_header": (15, "bold"),
        "stamp": (22, "bold"), "footer": (13, "regular"),
    }
    out: dict[str, Any] = {}
    for name, (size, kind) in sizes.items():
        path = paths.get(kind) or paths["regular"]
        out[name] = ImageFont.truetype(path, max(9, int(size * scale)))
    return out


# ---------------------------------------------------------------------------
# Clean rendering pass
# ---------------------------------------------------------------------------

INK = (28, 30, 34)
PAPER_CLEAN = (255, 255, 255)


def _wrap_text(draw, text: str, font, max_w: float) -> list[str]:  # noqa: ANN001
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        try:
            width = draw.textlength(trial, font=font)
        except Exception:
            width = font.getbbox(trial)[2]
        if width <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _render_clean_page(
    width: int,
    height: int,
    blocks: list,
    form: dict[str, Any] | None,
    rng: random.Random,
    scale: float,
):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), PAPER_CLEAN)
    draw = ImageDraw.Draw(img)
    fonts = _fonts(scale)
    margin = int(96 * scale)
    max_w = width - 2 * margin
    y = float(margin)

    line_h = {"h1": 44, "h2": 36, "h3": 30, "body": 27, "small": 22}

    def ensure_room(needed: float) -> None:
        nonlocal y
        if y + needed > height - margin:
            y = height - margin  # content beyond one page is clipped (caller splits pages)

    # structured form prelude (fields drawn under the title)
    for block in blocks:
        kind = block.kind
        if kind == "heading":
            f = fonts[block.level and {1: "h1", 2: "h2", 3: "h3"}[min(block.level, 3)]]
            lh = line_h[{1: "h1", 2: "h2", 3: "h3"}[min(block.level, 3)]]
            ensure_room(lh + 6)
            text = sanitize_plain(block.text)
            if block.level == 1:
                try:
                    tw = draw.textlength(text, font=f)
                except Exception:
                    tw = f.getbbox(text)[2]
                draw.text(((width - tw) / 2, y), text, font=f, fill=INK)
            else:
                draw.text((margin, y), text, font=f, fill=INK)
            y += lh
            if block.level == 1:
                draw.line([margin, y - 6, width - margin, y - 6], fill=(120, 124, 130), width=max(1, int(2 * scale)))
                y += 10
        elif kind == "paragraph":
            f = fonts["body"]
            plain = sanitize_plain("".join(s.text for s in block.inlines))
            if plain.startswith("[x]") or plain.startswith("[ ]"):
                # checkbox paragraph: [x]/[ ] first token becomes a drawn box
                for line in _wrap_text(draw, plain[3:].strip(), f, max_w - 30 * scale):
                    ensure_room(line_h["body"])
                    _draw_box(draw, margin, y, plain.startswith("[x]"), scale)
                    draw.text((margin + 28 * scale, y), line, font=f, fill=INK)
                    y += line_h["body"]
            else:
                for runs in _wrap_spans(draw, block.inlines, max_w, f, fonts["h3"]):
                    ensure_room(line_h["body"])
                    cx = float(margin)
                    for word, wf in runs:
                        draw.text((cx, y), word, font=wf, fill=INK)
                        try:
                            cx += draw.textlength(word, font=wf) + draw.textlength(" ", font=f)
                        except Exception:
                            cx += wf.getbbox(word)[2] + 6
                    y += line_h["body"]
            y += 6
        elif kind in ("bullet", "ordered"):
            f = fonts["body"]
            for idx, item in enumerate(block.items):
                text = sanitize_plain(item)
                if text.startswith("[x]") or text.startswith("[ ]"):
                    for j, line in enumerate(_wrap_text(draw, text[3:].strip(), f, max_w - 56 * scale)):
                        ensure_room(line_h["body"])
                        if j == 0:
                            _draw_box(draw, margin + 8 * scale, y, text.startswith("[x]"), scale)
                        draw.text((margin + 40 * scale, y), line, font=f, fill=INK)
                        y += line_h["body"]
                    continue
                marker = "-" if kind == "bullet" else f"{idx + 1}."
                for j, line in enumerate(_wrap_text(draw, text, f, max_w - 30 * scale)):
                    ensure_room(line_h["body"])
                    if j == 0:
                        draw.text((margin, y), marker, font=f, fill=INK)
                    draw.text((margin + 26 * scale, y), line, font=f, fill=INK)
                    y += line_h["body"]
            y += 6
        elif kind == "table":
            rows = block.rows
            if not rows:
                continue
            f = fonts["table"]
            fh = fonts["table_header"]
            col_w = _col_widths(draw, rows, max_w, fh, f)
            row_h = int(30 * scale)
            ensure_room(row_h * (min(len(rows), 4) + 1))
            x = margin
            for ci, col in enumerate(rows[0]):
                draw.rectangle([x, y, x + col_w[ci], y + row_h], outline=(90, 94, 100), width=max(1, int(1.2 * scale)))
                draw.rectangle([x, y, x + col_w[ci], y + row_h], fill=(238, 238, 236))
                draw.text((x + 8 * scale, y + 6 * scale), sanitize_plain(str(col))[: int(col_w[ci] / (9 * scale))], font=fh, fill=INK)
                x += col_w[ci]
            y += row_h
            for row in rows[1:]:
                x = margin
                ensure_room(row_h)
                for ci in range(len(col_w)):
                    cell = sanitize_plain(str(row[ci])) if ci < len(row) else ""
                    draw.rectangle([x, y, x + col_w[ci], y + row_h], outline=(90, 94, 100), width=max(1, int(1.2 * scale)))
                    draw.text((x + 8 * scale, y + 6 * scale), cell[: int(col_w[ci] / (9 * scale))], font=f, fill=INK)
                    x += col_w[ci]
                y += row_h
            y += 10
        elif kind == "quote":
            f = fonts["field_label"]
            for line in _wrap_text(draw, sanitize_plain(block.text), f, max_w - 24 * scale):
                ensure_room(line_h["small"])
                draw.text((margin + 12 * scale, y), line, font=f, fill=(90, 96, 104))
                y += line_h["small"]
            y += 6
        elif kind == "code":
            f = _mono_font(scale)
            for line in block.text.splitlines():
                ensure_room(line_h["small"])
                draw.text((margin, y), sanitize_plain(line), font=f, fill=INK)
                y += line_h["small"]
            y += 6
        elif kind == "rule":
            ensure_room(20)
            draw.line([margin, y, width - margin, y], fill=(150, 154, 160), width=max(1, int(1.2 * scale)))
            y += 16

    if form:
        y = _render_form(draw, form, margin, y, max_w, width, height, rng, scale, fonts)

    return img


def _mono_font(scale: float):
    from PIL import ImageFont

    paths = _font_paths()
    return ImageFont.truetype(paths.get("mono") or paths["regular"], max(9, int(15 * scale)))


def _draw_box(draw, x: float, y: float, checked: bool, scale: float) -> None:  # noqa: ANN001
    box = int(16 * scale)
    draw.rectangle([x, y + 4, x + box, y + 4 + box], outline=INK, width=max(1, int(1.6 * scale)))
    if checked:
        draw.line([x + 3, y + 7, x + box - 3, y + box + 1], fill=INK, width=max(1, int(2 * scale)))
        draw.line([x + box - 3, y + 7, x + 3, y + box + 1], fill=INK, width=max(1, int(2 * scale)))


def _wrap_spans(draw, inlines, max_w: float, base_font, bold_font) -> list[list[tuple[str, Any]]]:  # noqa: ANN001
    """Greedy word wrap over styled inline spans -> lines of (word, font)."""
    try:
        space_w = draw.textlength(" ", font=base_font)
    except Exception:
        space_w = 6.0
    words: list[tuple[str, Any]] = []
    for span in inlines:
        text = sanitize_plain(span.text)
        if not text:
            continue
        f = bold_font if span.bold else base_font
        for w in text.split(" "):
            if w:
                words.append((w, f))
    lines: list[list[tuple[str, Any]]] = []
    cur: list[tuple[str, Any]] = []
    cur_w = 0.0
    for w, f in words:
        try:
            ww = draw.textlength(w, font=f)
        except Exception:
            ww = f.getbbox(w)[2]
        add = ww + (space_w if cur else 0.0)
        if cur_w + add <= max_w or not cur:
            cur.append((w, f))
            cur_w += add
        else:
            lines.append(cur)
            cur = [(w, f)]
            cur_w = ww
    if cur:
        lines.append(cur)
    return lines or [[("", base_font)]]


def _draw_inline_checkboxes(draw, line, margin, y, f, scale) -> None:  # noqa: ANN001
    cx = margin
    token = ""
    for word in line.split(" "):
        if word in ("[x]", "[ ]"):
            if token:
                draw.text((cx, y), token, font=f, fill=INK)
                try:
                    cx += draw.textlength(token, font=f)
                except Exception:
                    cx += f.getbbox(token)[2]
                token = ""
            box = int(16 * scale)
            checked = word == "[x]"
            draw.rectangle([cx, y + 4, cx + box, y + 4 + box], outline=INK, width=max(1, int(1.6 * scale)))
            if checked:
                draw.line([cx + 3, y + 7, cx + box - 3, y + box + 1], fill=INK, width=max(1, int(2 * scale)))
                draw.line([cx + box - 3, y + 7, cx + 3, y + box + 1], fill=INK, width=max(1, int(2 * scale)))
            cx += box + 10
        else:
            token += word + " "
    if token:
        draw.text((cx, y), token.rstrip(), font=f, fill=INK)


def _col_widths(draw, rows, max_w, header_font, body_font) -> list[float]:  # noqa: ANN001
    ncols = max(len(r) for r in rows)
    weights = []
    for c in range(ncols):
        longest = 0
        for r in rows:
            cell = str(r[c]) if c < len(r) else ""
            f = header_font if r is rows[0] else body_font
            try:
                w = draw.textlength(sanitize_plain(cell), font=f)
            except Exception:
                w = f.getbbox(sanitize_plain(cell))[2]
            longest = max(longest, w)
        weights.append(max(60.0, min(longest + 20, max_w / 2)))
    total = sum(weights)
    return [max_w * w / total for w in weights]


def _render_form(draw, form: dict, margin, y, max_w, width, height, rng, scale, fonts):  # noqa: ANN001
    """Structured form: labeled fields (handwriting-ish values), checkboxes,
    signature scribble and an optional stamp. Returns the final y."""
    line_h = int(40 * scale)

    for field in form.get("fields") or []:
        label = sanitize_plain(str(field.get("label", "")))
        value = sanitize_plain(str(field.get("value", "")))
        f_lab = fonts["field_label"]
        f_val = fonts["field_value"]
        ensure = y + line_h < height - margin
        if not ensure:
            break
        draw.text((margin, y), label + ":", font=f_lab, fill=INK)
        try:
            lw = draw.textlength(label + ": ", font=f_lab)
        except Exception:
            lw = f_lab.getbbox(label + ": ")[2]
        vx = margin + lw + 8 * scale
        baseline = y + int(24 * scale)
        # fill line
        line_end = margin + max_w if field.get("full", False) else min(vx + max(120 * scale, int(len(value) * 12 * scale) + 40 * scale), margin + max_w)
        draw.line([margin, baseline, line_end, baseline], fill=(140, 144, 150), width=max(1, int(1.1 * scale)))
        if value:
            _handwriting(draw, value, vx, baseline - int(20 * scale), rng, scale, f_val)
        y += line_h

    for cb in form.get("checkboxes") or []:
        if y + line_h > height - margin:
            break
        box = int(18 * scale)
        checked = bool(cb.get("checked"))
        draw.rectangle([margin, y + 4, margin + box, y + 4 + box], outline=INK, width=max(1, int(1.6 * scale)))
        if checked:
            draw.line([margin + 3, y + 8, margin + box - 3, y + box + 1], fill=INK, width=max(1, int(2.2 * scale)))
            draw.line([margin + box - 3, y + 8, margin + 3, y + box + 1], fill=INK, width=max(1, int(2.2 * scale)))
        f_lab = fonts["field_label"]
        draw.text((margin + box + 12 * scale, y), sanitize_plain(str(cb.get("label", ""))), font=f_lab, fill=INK)
        y += int(34 * scale)

    if form.get("signature"):
        y += int(30 * scale)
        _signature_scribble(draw, margin + int(20 * scale), y, rng, scale)
        y += int(56 * scale)

    stamp_text = str(form.get("stamp_text") or "").strip()
    if stamp_text:
        _stamp(draw._image, width, height, stamp_text, rng, scale)
    return y


def _handwriting(draw, text, x, y, rng, scale, font) -> None:  # noqa: ANN001
    """Per-character jitter + slight rotation illusion => filled-in look."""
    cx = x
    for ch in text:
        dx = rng.uniform(-1.2, 1.2) * scale
        dy = rng.uniform(-2.0, 2.0) * scale
        draw.text((cx + dx, y + dy), ch, font=font, fill=(35, 40, 70))
        try:
            cx += draw.textlength(ch, font=font) * rng.uniform(0.96, 1.06)
        except Exception:
            cx += font.getbbox(ch)[2] + 1


def _signature_scribble(draw, x, y, rng, scale) -> None:  # noqa: ANN001
    """A plausible-looking seed-generated signature squiggle."""
    pts = []
    cx, cy = float(x), float(y)
    angle = -0.6
    for _ in range(rng.randint(26, 40)):
        angle += rng.uniform(-0.9, 0.9)
        step = rng.uniform(6, 16) * scale
        cx += math.cos(angle) * step
        cy += math.sin(angle) * step * 0.55
        cy = min(max(cy, y - 26 * scale), y + 20 * scale)
        pts.append((cx, cy))
    draw.line(pts, fill=(25, 30, 60), width=max(2, int(2.4 * scale)), joint="curve")
    end = pts[-1]
    draw.line([end, (end[0] + 30 * scale, end[1])], fill=(25, 30, 60), width=max(2, int(2 * scale)))


def _stamp(base_img, width, height, text, rng, scale) -> None:  # noqa: ANN001
    """Rotated muted-red stamp with slight transparency (drawn on an overlay)."""
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    lines = [t.strip().upper() for t in text.split("|") if t.strip()][:3]
    pad = int(14 * scale)
    f = _fonts(scale)["stamp"]
    try:
        w_max = max(od.textlength(t, font=f) for t in lines)
    except Exception:
        w_max = max(f.getbbox(t)[2] for t in lines)
    box_w = int(w_max + 2 * pad)
    box_h = int(len(lines) * 30 * scale + 2 * pad)
    cx = int(width * rng.uniform(0.58, 0.8))
    cy = int(height * rng.uniform(0.72, 0.88))
    color = (150, 45, 45, rng.randint(120, 170))
    od.rounded_rectangle([0, 0, box_w, box_h], radius=int(10 * scale), outline=color, width=max(2, int(3 * scale)))
    for i, t in enumerate(lines):
        try:
            tw = od.textlength(t, font=f)
        except Exception:
            tw = f.getbbox(t)[2]
        od.text(((box_w - tw) / 2, pad + i * 30 * scale - 2), t, font=f, fill=color)
    angle = rng.uniform(-9, -4)
    overlay = overlay.rotate(angle, expand=True, resample=Image.BICUBIC)
    base_img.paste(overlay, (cx - overlay.width // 2, cy - overlay.height // 2), overlay)


# ---------------------------------------------------------------------------
# Degradation pipeline (the actual "scan")
# ---------------------------------------------------------------------------


def _find_coeffs(pa: list[tuple[float, float]], pb: list[tuple[float, float]]) -> tuple[float, ...]:
    """Solve the 8-unknown perspective transform (standard technique)."""
    matrix = []
    for (x, y), (X, Y) in zip(pa, pb):
        matrix.append([X, Y, 1, 0, 0, 0, -x * X, -x * Y])
        matrix.append([0, 0, 0, X, Y, 1, -y * X, -y * Y])
    A = matrix
    b = [c for p in pa for c in p]
    n = len(A)
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(A[r][i]))
        A[i], A[piv] = A[piv], A[i]
        b[i], b[piv] = b[piv], b[i]
        for r in range(i + 1, n):
            factor = A[r][i] / A[i][i]
            for c in range(i, n):
                A[r][c] -= factor * A[i][c]
            b[r] -= factor * b[i]
    sol = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = b[i] - sum(A[i][c] * sol[c] for c in range(i + 1, n))
        sol[i] = s / A[i][i]
    return tuple(sol)


def _degrade(img, rng: random.Random, mode: str, dpi_scale: float):
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter

    w, h = img.size

    # 1) paper tint (slightly warm, per-page variation)
    tint_base = (252, 250, 244) if mode != "photocopy" else (250, 250, 248)
    tint = tuple(int(c * rng.uniform(0.985, 1.0)) for c in tint_base)
    paper = Image.new("RGB", (w, h), tint)
    img = ImageChops.multiply(img, paper)

    # 2) rotation (skewed feed)
    angle = rng.uniform(0.4, 1.6) if mode == "scan" else rng.uniform(1.2, 3.2)
    if mode == "photocopy":
        angle = rng.uniform(-0.9, 0.9)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=tint)

    # 3) perspective skew for photos (hand-held document)
    if mode == "photo":
        dev = min(w, h) * rng.uniform(0.015, 0.045)
        src = [(0, 0), (w, 0), (w, h), (0, h)]
        dst = [
            (rng.uniform(0, dev), rng.uniform(0, dev)),
            (w - rng.uniform(0, dev), rng.uniform(0, dev * 0.6)),
            (w - rng.uniform(0, dev * 0.6), h - rng.uniform(0, dev)),
            (rng.uniform(0, dev * 0.6), h - rng.uniform(0, dev)),
        ]
        coeffs = _find_coeffs(dst, src)
        img = img.transform((w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=tint)

    # 4) brightness gradient / shadow (scanner lid not perfect / phone shadow)
    grad = Image.linear_gradient("L").rotate(rng.choice([0, 90, 180, 270])).resize((w, h))
    strength = {"scan": rng.uniform(0.90, 0.97), "photo": rng.uniform(0.78, 0.92), "photocopy": rng.uniform(0.92, 0.99)}[mode]
    dark = grad.point(lambda v: int(255 * (strength + (1 - strength) * v / 255)))
    img = ImageChops.multiply(img, Image.merge("RGB", (dark, dark, dark)))

    # 5) vignette for photos
    if mode == "photo":
        mask = Image.new("L", (w, h), 0)
        from PIL import ImageDraw

        md = ImageDraw.Draw(mask)
        md.ellipse([-w * 0.25, -h * 0.25, w * 1.25, h * 1.25], fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(min(w, h) // 6))
        dark_center = ImageChops.multiply(img, Image.merge("RGB", (mask, mask, mask)))
        img = Image.blend(img, dark_center, rng.uniform(0.35, 0.6))

    # 6) sensor noise - generated from the SEEDED rng so the scan is
    # reproducible (Image.effect_noise uses an unseedable global RNG).
    sigma = {"scan": rng.uniform(6, 12), "photo": rng.uniform(10, 18), "photocopy": rng.uniform(8, 14)}[mode]
    nw, nh = max(2, w // 2), max(2, h // 2)
    noise_l = Image.new("L", (nw, nh))
    noise_l.putdata([max(0, min(255, int(128 + rng.gauss(0, sigma)))) for _ in range(nw * nh)])
    noise = noise_l.resize((w, h), Image.BILINEAR)
    img = ImageChops.add(img, Image.merge("RGB", (noise, noise, noise)), scale=1, offset=-128)

    # 7) slight blur (focus / lens)
    blur = {"scan": rng.uniform(0.3, 0.7), "photo": rng.uniform(0.7, 1.3), "photocopy": rng.uniform(0.2, 0.5)}[mode]
    img = img.filter(ImageFilter.GaussianBlur(blur * dpi_scale))

    # 8) photocopy: harsh contrast, near-BW
    if mode == "photocopy":
        img = ImageEnhance.Contrast(img.convert("L")).enhance(rng.uniform(1.6, 2.2)).convert("RGB")

    # 9) JPEG re-compression (the defining scanner artifact)
    quality = {"scan": rng.randint(52, 70), "photo": rng.randint(42, 60), "photocopy": rng.randint(48, 66)}[mode]
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    img = Image.open(buf).convert("RGB")
    return img


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_scanned_image(
    dest: str | Path,
    content_md: str = "",
    *,
    form: dict[str, Any] | None = None,
    mode: str = "scan",
    seed: int = 7,
    dpi: int = 150,
    width_px: int | None = None,
    height_px: int | None = None,
) -> dict[str, Any]:
    """Render one scanned page (PNG/JPG by extension). Deterministic."""
    from PIL import Image

    mode = (mode or "scan").lower()
    if mode not in ("scan", "photo", "photocopy"):
        raise ValueError(f"unknown mode {mode!r} (scan | photo | photocopy)")
    rng = random.Random(seed)
    width = width_px or int(8.27 * dpi)   # A4
    height = height_px or int(11.69 * dpi)
    scale = width / 1240.0

    blocks = parse_markdown(content_md) if content_md else []
    clean = _render_clean_page(width, height, blocks, form, rng, scale)
    final = _degrade(clean, rng, mode, scale)

    name = getattr(dest, "name", "")
    if hasattr(dest, "write"):  # file-like (BytesIO for the PDF writer)
        fmt = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}.get(
            str(name).lower().rsplit(".", 1)[-1] if "." in str(name) else "png", "PNG")
        if fmt == "JPEG":
            final.save(dest, format="JPEG", quality=88)
        else:
            final.save(dest, format="PNG")
        return {"size": [final.width, final.height], "mode": mode, "seed": seed}
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}.get(dest.suffix.lower().lstrip("."), "PNG")
    if fmt == "JPEG":
        final.save(str(dest), format="JPEG", quality=88)
    else:
        final.save(str(dest), format="PNG")
    return {"bytes": dest.stat().st_size, "size": [final.width, final.height], "mode": mode, "seed": seed}


def write_scanned_pdf(
    dest: str | Path,
    content_md: str = "",
    *,
    form: dict[str, Any] | None = None,
    form_pages: list[dict[str, Any]] | None = None,
    mode: str = "scan",
    seed: int = 7,
    dpi: int = 150,
    title: str = "",
) -> dict[str, Any]:
    """Multi-page scanned-style PDF. Page breaks: ``<<<PAGEBREAK>>>`` in the
    markdown, or ``form_pages`` (one form spec per page). Deterministic."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as rl_canvas
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"reportlab is required for scanned PDFs ({exc})") from exc

    pages_md = [p.strip() for p in content_md.split(PAGEBREAK_MARKER)] if content_md else []
    if not pages_md and not form_pages and not form:
        raise ValueError("nothing to render: pass content_md, form or form_pages")
    if form and not form_pages and not pages_md:
        pages_md = [""]

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    page_specs: list[tuple[str, dict | None]] = [(p, None) for p in pages_md]
    if form_pages:
        page_specs = [(fp.get("content_md", ""), {**form, **fp} if form else fp) for fp in form_pages]
    elif form and pages_md:
        page_specs = [(p, form if i == 0 else None) for i, p in enumerate(pages_md)]

    rng = random.Random(seed)
    c = rl_canvas.Canvas(str(dest), pagesize=A4)
    W, H = A4
    rendered = 0
    for i, (md, page_form) in enumerate(page_specs):
        buf = io.BytesIO()
        render_scanned_image(
            buf, md, form=page_form, mode=mode, seed=seed + 977 * i, dpi=dpi,
            width_px=int(8.27 * dpi), height_px=int(11.69 * dpi),
        )
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=W, height=H)
        c.showPage()
        rendered += 1
    if title:
        c.setTitle(title)
    c.save()
    return {"bytes": dest.stat().st_size, "pages": rendered, "mode": mode, "seed": seed}
