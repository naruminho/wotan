"""Image utilities on Pillow (the ``pillow`` extra).

- :func:`transform_image`: resize / crop / rotate / convert / grayscale /
  flip / brightness / contrast / text watermark / border - a chain of ops.
- :func:`satellite_mosaic`: fetch real satellite imagery around a lat/lon as a
  tile mosaic (slippy-map math), from a CONFIGURED tile provider (default:
  Esri World Imagery; NASA GIBS works too). The host allow-list lives in
  ``config.artifacts.satellite.allowed_hosts`` - never arbitrary URLs.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger("wotan.artifacts.images", component="artifacts")

_POSITIONS = {
    "center": (0.5, 0.5),
    "top-left": (0.06, 0.06), "top-right": (0.94, 0.06),
    "bottom-left": (0.06, 0.94), "bottom-right": (0.94, 0.94),
}


def _load(path: str | Path):
    from PIL import Image

    img = Image.open(str(path))
    img.load()
    return img


def transform_image(src: str | Path, dest: str | Path, ops: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFont
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"pillow is required for image transforms ({exc})") from exc

    img = _load(src)
    applied: list[str] = []
    for op in ops or []:
        name = str(op.get("op", "")).lower()
        if name == "resize":
            w, h = op.get("width"), op.get("height")
            if w and not h:
                h = int(img.height * float(w) / img.width)
            if h and not w:
                w = int(img.width * float(h) / img.height)
            if not w and not h and op.get("scale"):
                w = int(img.width * float(op["scale"]))
                h = int(img.height * float(op["scale"]))
            if not w or not h:
                raise ValueError("resize needs width, height or scale")
            img = img.resize((int(w), int(h)), Image.LANCZOS)
            applied.append(f"resize {w}x{h}")
        elif name == "crop":
            box = [int(op[k]) for k in ("left", "top", "right", "bottom")]
            if not (0 <= box[0] < box[2] <= img.width and 0 <= box[1] < box[3] <= img.height):
                raise ValueError(f"crop box {box} is outside the image ({img.width}x{img.height})")
            img = img.crop(box)
            applied.append(f"crop {box}")
        elif name == "rotate":
            img = img.rotate(float(op.get("degrees", 90)), expand=True)
            applied.append(f"rotate {op.get('degrees', 90)}")
        elif name == "grayscale":
            img = img.convert("L").convert("RGB")
            applied.append("grayscale")
        elif name == "flip_h":
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
            applied.append("flip_h")
        elif name == "flip_v":
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
            applied.append("flip_v")
        elif name == "brightness":
            img = ImageEnhance.Brightness(img).enhance(float(op.get("factor", 1.2)))
            applied.append(f"brightness x{op.get('factor', 1.2)}")
        elif name == "contrast":
            img = ImageEnhance.Contrast(img).enhance(float(op.get("factor", 1.2)))
            applied.append(f"contrast x{op.get('factor', 1.2)}")
        elif name == "watermark":
            text = str(op.get("text", ""))
            if not text:
                raise ValueError("watermark needs 'text'")
            if img.mode != "RGB":
                img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            size = max(12, int(img.height * float(op.get("size", 0.05))))
            font = None
            import os

            for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         os.path.expandvars(r"%WINDIR%\Fonts\arialbd.ttf")):
                if os.path.isfile(cand):
                    font = ImageFont.truetype(cand, size)
                    break
            if font is None:
                font = ImageFont.load_default()
            alpha = float(op.get("opacity", 0.45))
            gray = int(255 - (255 - 40) * alpha)
            px, py = _POSITIONS.get(str(op.get("position", "bottom-right")), _POSITIONS["bottom-right"])
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = int(img.width * px - tw * (px / max(px, 1e-6) * 0.5 + 0.0)) if False else int(img.width * px) - (tw if px > 0.5 else 0)
            y = int(img.height * py) - (th if py > 0.5 else 0)
            # subtle shadow for readability
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0))
            draw.text((x, y), text, font=font, fill=(gray, gray, gray))
            applied.append(f"watermark {text!r}")
        elif name == "border":
            wpx = int(op.get("width", 4))
            color = op.get("color", "#1F3A5F")
            if isinstance(color, str) and color.startswith("#"):
                color = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            if img.mode != "RGB":
                img = img.convert("RGB")
            from PIL import ImageOps

            img = ImageOps.expand(img, border=wpx, fill=color)  # type: ignore[arg-type]
            applied.append(f"border {wpx}px")
        elif name == "convert":
            applied.append(f"convert {op.get('format', '')}")
        else:
            raise ValueError(f"unknown image op {name!r} (resize|crop|rotate|grayscale|flip_h|flip_v|brightness|contrast|watermark|border)")

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "bmp": "BMP", "tif": "TIFF", "tiff": "TIFF"}.get(
        dest.suffix.lower().lstrip("."), "PNG")
    save_img = img
    if fmt == "JPEG" and save_img.mode in ("RGBA", "P", "LA"):
        save_img = save_img.convert("RGB")
    quality = 92
    for op in ops or []:
        if str(op.get("op", "")).lower() == "convert" and op.get("quality"):
            quality = int(op["quality"])
    save_kwargs: dict[str, Any] = {"format": fmt}
    if fmt in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
    save_img.save(str(dest), **save_kwargs)
    return {"bytes": dest.stat().st_size, "size": [save_img.width, save_img.height], "applied": applied}


# ---------------------------------------------------------------------------
# Satellite tile mosaic (slippy map / Web Mercator)
# ---------------------------------------------------------------------------

def _deg2tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def satellite_mosaic(
    dest: str | Path,
    lat: float,
    lon: float,
    *,
    zoom: int = 18,
    tiles: int = 2,
    base_url: str = "",
    allowed_hosts: list[str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    attribution: str = "Imagens: Esri, Maxar, Earthstar Geographics",
    user_agent: str = "wotan-artifacts/0.1",
) -> dict[str, Any]:
    """Download a (2*tiles)x(2*tiles) mosaic of satellite tiles centered on
    (lat, lon) and stitch it into one JPEG/PNG.

    ``base_url`` must be a template containing {z}/{x}/{y}. The host is checked
    against ``allowed_hosts`` (SSRF guard). Example Esri template:
    https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
    """
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(f"pillow is required for satellite mosaics ({exc})") from exc
    import httpx

    if not (-85.0 <= lat <= 85.0 and -180.0 <= lon <= 180.0):
        raise ValueError(f"lat/lon out of range: ({lat}, {lon})")
    zoom = max(1, min(int(zoom), 21))
    tiles = max(1, min(int(tiles), 3))  # up to 6x6 = 36 tiles
    if not base_url or "{z}" not in base_url:
        raise ValueError("satellite base_url must be a template containing {z} (and {x}/{y})")

    from urllib.parse import urlparse

    host = urlparse(base_url).hostname or ""
    if allowed_hosts and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise PermissionError(
            f"tile host {host!r} is not in artifacts.satellite.allowed_hosts - "
            "add it to the config (never fetch tiles from arbitrary hosts)"
        )

    cx, cy = _deg2tile(lat, lon, zoom)
    cx_i, cy_i = int(cx), int(cy)
    n = 2.0 ** zoom
    size = 256
    mosaic = Image.new("RGB", (size * tiles * 2, size * tiles * 2), (20, 24, 28))
    fetched, failed = 0, 0
    req_headers = {"User-Agent": user_agent, **(headers or {})}
    with httpx.Client(timeout=timeout, headers=req_headers, follow_redirects=True) as client:
        for dy in range(-tiles, tiles):
            for dx in range(-tiles, tiles):
                tx, ty = cx_i + dx, cy_i + dy
                if ty < 0 or ty >= n:
                    continue
                tx_norm = tx % int(n)
                url = base_url.format(z=zoom, x=tx_norm, y=ty)
                try:
                    resp = client.get(url)
                    if resp.status_code == 200 and resp.content:
                        tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
                        mosaic.paste(tile, ((dx + tiles) * size, (dy + tiles) * size))
                        fetched += 1
                    else:
                        failed += 1
                except Exception as exc:
                    log.warning("tile fetch failed", extra={"data": {"url": url, "error": str(exc)}})
                    failed += 1
    if fetched == 0:
        raise ConnectionError(
            f"no satellite tiles could be fetched (HTTP failures: {failed}) - "
            "check the network, the provider URL in config.yaml or provide a local image instead"
        )
    # the mosaic is fetched around the point: the central tile contains
    # (lat, lon), so the full mosaic is already the area of interest
    cropped = mosaic
    # attribution bar (required by most providers)
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(cropped)
    bar_h = max(18, cropped.height // 30)
    draw.rectangle([0, cropped.height - bar_h, cropped.width, cropped.height], fill=(0, 0, 0))
    font = None
    import os

    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", os.path.expandvars(r"%WINDIR%\Fonts\arial.ttf")):
        if os.path.isfile(cand):
            font = ImageFont.truetype(cand, bar_h - 6)
            break
    if font is None:
        font = ImageFont.load_default()
    draw.text((6, cropped.height - bar_h + 2), attribution, font=font, fill=(230, 230, 230))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if dest.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    (cropped.convert("RGB") if fmt == "JPEG" else cropped).save(str(dest), format=fmt, quality=90)
    return {"bytes": dest.stat().st_size, "size": [cropped.width, cropped.height],
            "zoom": zoom, "tiles_fetched": fetched, "tiles_failed": failed, "provider_host": host}
