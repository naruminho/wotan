"""Chart PNGs drawn with Pillow (the ``pillow`` extra) - no heavier plotting
stack needed, works offline, deterministic output.

Kinds: bar, hbar, line, area, pie, donut, scatter, histogram. Rendered at 3x
supersampling and downscaled with Lanczos for smooth anti-aliased output.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

PALETTES = {
    "slate": ["#37424E", "#5B6B7A", "#8494A3", "#A8B4BF", "#6E7F8D", "#4A5A6A", "#93A1AD", "#B0885A"],
    "blue": ["#1F3A5F", "#2E6DA4", "#5CB85C", "#F0AD4E", "#D9534F", "#7B68AE", "#17A2B8", "#8C564B"],
    "green": ["#1E6B45", "#3E9B6F", "#83C69A", "#F0AD4E", "#D9534F", "#5B8BC2", "#7B6CA8", "#8C6D4B"],
    "warm": ["#8C4A2F", "#C97B3D", "#E3B23C", "#7B8D42", "#4E7A6A", "#94516B", "#5B5B8C", "#8C6D4B"],
}

_KINDS = {"bar", "hbar", "line", "area", "pie", "donut", "scatter", "histogram"}
_SS = 3  # supersampling factor


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 8:
        color = color[:6]
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _font(size_px: int):
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    import os

    windir = os.path.expandvars(r"%WINDIR%\Fonts\arial.ttf")
    if os.path.isfile(windir):
        candidates.insert(0, windir)
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size_px)
            except Exception:
                continue
    return ImageFont.load_default()


def _text(draw, xy, text, size, fill, anchor="la", bold=False):  # noqa: ANN001
    from PIL import ImageDraw

    f = _font(size)
    draw.text(xy, text, font=f, fill=fill, anchor=anchor)


def write_chart(
    dest: str | Path,
    kind: str,
    *,
    labels: list[str] | None = None,
    series: list[dict[str, Any]] | None = None,
    title: str = "",
    width: int = 900,
    height: int = 560,
    palette: str = "blue",
    bins: int = 10,
    y_label: str = "",
    x_label: str = "",
    donut_hole: float = 0.55,
) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"pillow is required to draw charts ({exc})") from exc

    kind = (kind or "bar").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"unknown chart kind {kind!r} - supported: {', '.join(sorted(_KINDS))}")
    series = series or []
    if not series:
        raise ValueError("series is empty: pass [{name, values}]")
    values = [float(v) for s in series for v in s.get("values") or []]
    if kind in ("scatter",):
        if len(series) < 2:
            raise ValueError("scatter needs two series (x values and y values)")
        xs = [float(v) for v in series[0]["values"]]
        ys = [float(v) for v in series[1]["values"]]
        if len(xs) != len(ys) or not xs:
            raise ValueError("scatter series must have the same non-zero length")
    elif not values and kind != "scatter":
        raise ValueError("series values are empty")

    colors = PALETTES.get(palette, PALETTES["blue"])
    W, H = int(width) * _SS, int(height) * _SS
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    margin_l, margin_r, margin_t, margin_b = 90 * _SS, 40 * _SS, 70 * _SS, 90 * _SS
    plot_w, plot_h = W - margin_l - margin_r, H - margin_t - margin_b
    fg = (43, 51, 61)
    grid = (225, 229, 234)

    if title:
        _text(draw, (margin_l, int(18 * _SS)), title, 15 * _SS, fg, bold=True)

    def nice_max(v: float) -> float:
        if v <= 0:
            return 1.0
        exp = math.floor(math.log10(v))
        base = 10 ** exp
        for m in (1, 2, 2.5, 5, 10):
            if v <= m * base:
                return m * base
        return 10 * base

    def fmt(v: float) -> str:
        if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
            return f"{int(round(v))}"
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if kind in ("bar", "line", "area"):
        labels = labels or [str(i + 1) for i in range(max(len(s.get("values") or []) for s in series))]
        vmax = nice_max(max(values + [0.0]))
        vmin = 0.0
        # gridlines + y ticks
        for i in range(6):
            y = margin_t + plot_h * i / 5
            draw.line([(margin_l, y), (W - margin_r, y)], fill=grid, width=_SS)
            _text(draw, (margin_l - 8 * _SS, y), fmt(vmax - (vmax - vmin) * i / 5), 9 * _SS, (110, 120, 130), anchor="rm")
        if y_label:
            _text(draw, (margin_l, int(40 * _SS)), y_label, 9 * _SS, (110, 120, 130))
        ncat = max(len(labels), 1)
        slot = plot_w / ncat
        bar_w = slot * 0.62 / max(1, len(series))
        for si, s in enumerate(series):
            color = _hex(s.get("color") or colors[si % len(colors)])
            vals = [float(v) for v in (s.get("values") or [])]
            if kind == "bar":
                for ci, v in enumerate(vals):
                    x0 = margin_l + ci * slot + slot * 0.19 + si * bar_w
                    hpx = plot_h * (v - vmin) / (vmax - vmin)
                    draw.rectangle([x0, margin_t + plot_h - hpx, x0 + bar_w * 0.92, margin_t + plot_h], fill=color)
                    if len(vals) <= 24:
                        _text(draw, (x0 + bar_w * 0.46, margin_t + plot_h - hpx - 6 * _SS), fmt(v), 8 * _SS, fg, anchor="ms")
            else:  # line / area
                pts = []
                for ci, v in enumerate(vals):
                    x = margin_l + (ci + 0.5) * slot
                    y = margin_t + plot_h - plot_h * (v - vmin) / (vmax - vmin)
                    pts.append((x, y))
                if kind == "area" and pts:
                    draw.polygon(pts + [(pts[-1][0], margin_t + plot_h), (pts[0][0], margin_t + plot_h)], fill=_hex(s.get("color") or colors[si % len(colors)]) + (0,))
                    # Pillow RGB has no alpha in draw.polygon fill tuple -> use light blend
                for a, b in zip(pts, pts[1:]):
                    draw.line([a, b], fill=color, width=2 * _SS, joint="curve")
                for x, y in pts:
                    r = 3 * _SS
                    draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline="white", width=_SS)
        # x labels
        for ci, lab in enumerate(labels[:60]):
            _text(draw, (margin_l + (ci + 0.5) * slot, margin_t + plot_h + 8 * _SS), str(lab)[:14], 8 * _SS, fg, anchor="ma")
        if x_label:
            _text(draw, (margin_l + plot_w / 2, H - 22 * _SS), x_label, 9 * _SS, (110, 120, 130), anchor="mm")

    elif kind == "hbar":
        labels = labels or [str(i + 1) for i in range(max(len(s.get("values") or []) for s in series))]
        vals = [float(v) for v in (series[0].get("values") or [])]
        pairs = sorted(zip(labels[: len(vals)], vals), key=lambda p: p[1])
        vmax = nice_max(max(vals + [0.0]))
        ncat = max(len(pairs), 1)
        row_h = plot_h / ncat
        max_lab = max((len(str(l)) for l, _ in pairs), default=4)
        lab_w = min(220 * _SS, (max_lab + 2) * 8 * _SS)
        for i, (lab, v) in enumerate(pairs):
            y = margin_t + i * row_h
            _text(draw, (margin_l + lab_w - 6 * _SS, y + row_h / 2), str(lab)[:28], 9 * _SS, fg, anchor="rm")
            bar_h = min(row_h * 0.62, 34 * _SS)
            wpx = plot_w * 0.86 * (v / vmax)
            draw.rounded_rectangle([margin_l + lab_w, y + (row_h - bar_h) / 2, margin_l + lab_w + wpx, y + (row_h + bar_h) / 2],
                                   radius=bar_h // 4, fill=_hex(colors[0]))
            _text(draw, (margin_l + lab_w + wpx + 6 * _SS, y + row_h / 2), fmt(v), 8 * _SS, fg, anchor="lm")

    elif kind in ("pie", "donut"):
        vals = [float(v) for v in (series[0].get("values") or [])]
        labs = [str(l) for l in (labels or [str(i + 1) for i in range(len(vals))])]
        total = sum(vals) or 1.0
        cx, cy = margin_l + plot_w * 0.36, margin_t + plot_h / 2
        radius = min(plot_w * 0.34, plot_h * 0.46)
        start = -90.0
        for i, v in enumerate(vals):
            sweep = 360.0 * v / total
            color = colors[i % len(colors)]
            draw.pieslice([cx - radius, cy - radius, cx + radius, cy + radius], start, start + sweep, fill=_hex(color), outline="white", width=2 * _SS)
            if kind == "donut":
                r2 = radius * donut_hole
                draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill="white")
            mid = math.radians(start + sweep / 2)
            lx = cx + math.cos(mid) * radius * (0.72 if kind == "donut" else 0.66)
            ly = cy + math.sin(mid) * radius * (0.72 if kind == "donut" else 0.66)
            pct = 100.0 * v / total
            if pct >= 4:
                tcolor = "white" if kind == "pie" else fg
                _text(draw, (lx, ly), f"{pct:.0f}%", 9 * _SS, tcolor, anchor="mm")
            start += sweep
        # legend
        ly = margin_t
        for i, lab in enumerate(labs[:12]):
            color = colors[i % len(colors)]
            draw.rectangle([margin_l + plot_w * 0.72, ly, margin_l + plot_w * 0.72 + 14 * _SS, ly + 14 * _SS], fill=_hex(color))
            _text(draw, (margin_l + plot_w * 0.72 + 22 * _SS, ly + 7 * _SS), f"{lab[:22]} ({fmt(vals[i])})", 9 * _SS, fg, anchor="lm")
            ly += 22 * _SS

    elif kind == "scatter":
        xs = [float(v) for v in series[0]["values"]]
        ys = [float(v) for v in series[1]["values"]]
        xmax, xmin = nice_max(max(xs)), min(xs + [0.0])
        ymax, ymin = nice_max(max(ys)), min(ys + [0.0])
        for i in range(6):
            y = margin_t + plot_h * i / 5
            draw.line([(margin_l, y), (W - margin_r, y)], fill=grid, width=_SS)
            _text(draw, (margin_l - 8 * _SS, y), fmt(ymax - (ymax - ymin) * i / 5), 9 * _SS, (110, 120, 130), anchor="rm")
        color = _hex(series[1].get("color") or colors[0])
        for x, y in zip(xs, ys):
            px = margin_l + plot_w * (x - xmin) / (xmax - xmin)
            py = margin_t + plot_h - plot_h * (y - ymin) / (ymax - ymin)
            r = 3.4 * _SS
            draw.ellipse([px - r, py - r, px + r, py + r], fill=color + (0,) if False else color, outline="white", width=_SS)
        if len(xs) > 2:
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / max(sum((x - mx) ** 2 for x in xs), 1e-12)
            alpha = my - beta * mx
            x0, x1 = min(xs), max(xs)
            p0 = (margin_l + plot_w * (x0 - xmin) / (xmax - xmin), margin_t + plot_h - plot_h * (alpha + beta * x0 - ymin) / (ymax - ymin))
            p1 = (margin_l + plot_w * (x1 - xmin) / (xmax - xmin), margin_t + plot_h - plot_h * (alpha + beta * x1 - ymin) / (ymax - ymin))
            draw.line([p0, p1], fill=(120, 130, 140), width=2 * _SS)
        _text(draw, (margin_l + plot_w / 2, H - 22 * _SS), str(series[0].get("name") or x_label or "x"), 9 * _SS, (110, 120, 130), anchor="mm")

    elif kind == "histogram":
        nbins = max(2, min(int(bins), 60))
        lo, hi = min(values), max(values)
        if hi == lo:
            hi = lo + 1
        counts = [0] * nbins
        for v in values:
            idx = min(nbins - 1, int((v - lo) / (hi - lo) * nbins))
            counts[idx] += 1
        vmax = nice_max(max(counts + [1.0]))
        for i in range(6):
            y = margin_t + plot_h * i / 5
            draw.line([(margin_l, y), (W - margin_r, y)], fill=grid, width=_SS)
            _text(draw, (margin_l - 8 * _SS, y), fmt(vmax - vmax * i / 5), 9 * _SS, (110, 120, 130), anchor="rm")
        slot = plot_w / nbins
        for i, c in enumerate(counts):
            hpx = plot_h * c / vmax
            x0 = margin_l + i * slot + 1 * _SS
            draw.rectangle([x0, margin_t + plot_h - hpx, x0 + slot - 2 * _SS, margin_t + plot_h], fill=_hex(colors[1 % len(colors)]))
        for i in range(0, nbins + 1, max(1, nbins // 8)):
            x = margin_l + i * slot
            _text(draw, (x, margin_t + plot_h + 8 * _SS), fmt(lo + (hi - lo) * i / nbins), 8 * _SS, fg, anchor="ma")

    # legend for multi-series cartesian charts
    if kind in ("bar", "line", "area") and len(series) > 1:
        lx = W - margin_r - 10 * _SS
        ly = margin_t + 4 * _SS
        for si, s in enumerate(series):
            color = _hex(s.get("color") or colors[si % len(colors)])
            draw.rectangle([lx - 120 * _SS, ly, lx - 104 * _SS, ly + 14 * _SS], fill=color)
            _text(draw, (lx - 98 * _SS, ly + 7 * _SS), str(s.get("name") or f"serie {si + 1}")[:18], 9 * _SS, fg, anchor="lm")
            ly += 22 * _SS

    final = img.resize((int(width), int(height)), Image.LANCZOS)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final.save(str(dest), format="PNG", optimize=True)
    return {"bytes": dest.stat().st_size, "kind": kind, "size": [int(width), int(height)]}
