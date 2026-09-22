"""Image generation through the CONFIGURED multimodal LLM gateway.

Wotan never calls a vendor's public API directly: the request goes to the
provider configured in ``artifacts.image_generation`` (typically an
OpenAI-compatible ``/images/generations`` endpoint exposed by the corporate
gateway), reusing the provider's auth (TokenManager) exactly like the
external-workflow tools do.

Response shapes accepted (first match wins):
- ``{"data": [{"b64_json": "..."}]}``          (OpenAI images contract)
- ``{"data": [{"url": "https://..."}]}``       (URL is downloaded)
- ``{"image_base64": "..."}`` / ``{"image": "data:image/png;base64,..."}``
- a bare data-URL in a text field (``{"text": "data:image/png;base64,..."}``)

Prompt guidance lives in the tool description: photorealistic people
(natural skin texture, real camera optics) and sober flat infographics -
never the glossy neon style.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

log = get_logger("wotan.artifacts.llm_image", component="artifacts")

_DATA_URL = re.compile(r"data:image/[a-zA-Z]+;base64,([A-Za-z0-9+/=\s]+)")


def photoreal_directive(prompt: str, style_hint: str = "") -> str:
    """Return the prompt plus quality directives (does not override content)."""
    lower = prompt.lower()
    extras: list[str] = []
    people = any(w in lower for w in ("pessoa", "pessoas", "equipe", "retrato", "portrait", "woman", "man", "team", "cliente", "funcion", "público", "publico"))
    graphics = any(w in lower for w in ("infográfico", "infografico", "esquema", "diagrama", "ícone", "icone", "ilustra", "slide", "banner", "diagram", "infographic"))
    if people and "foto" not in lower and "desenho" not in lower and "illustration" not in lower:
        extras.append(
            "fotografia realista, câmera profissional 85mm f/2.0, luz natural suave, "
            "textura de pele real, expressões naturais, sem suavização artificial, sem plástico"
        )
    if graphics:
        extras.append(
            "design editorial minimalista e sóbrio: paleta de cores dessaturada, muito espaço em branco, "
            "linhas finas, tipografia limpa; sem néon, sem gradiente espelhado, sem brilho 3D, sem efeito IA"
        )
    if style_hint:
        extras.append(style_hint)
    return prompt if not extras else prompt + "\n\nEstilo: " + "; ".join(extras) + "."


def decode_image_payload(data: Any) -> bytes | None:
    """Extract image bytes from the supported response shapes (None if absent)."""
    if not isinstance(data, dict):
        return None
    arr = data.get("data")
    if isinstance(arr, list) and arr:
        first = arr[0]
        if isinstance(first, dict):
            b64 = first.get("b64_json") or first.get("b64") or first.get("image_base64")
            if b64:
                return base64.b64decode(re.sub(r"\s", "", str(b64)))
            url = first.get("url")
            if url:
                return url  # caller distinguishes: URL string, not bytes
    for key in ("image_base64", "b64_json", "image", "output"):
        v = data.get(key)
        if isinstance(v, str):
            m = _DATA_URL.search(v)
            if m:
                return base64.b64decode(re.sub(r"\s", "", m.group(1)))
            if key != "image" or v.startswith("data:"):
                try:
                    return base64.b64decode(re.sub(r"\s", "", v))
                except Exception:
                    continue
    for text in (data.get("text"), data.get("content")):
        if isinstance(text, str):
            m = _DATA_URL.search(text)
            if m:
                return base64.b64decode(re.sub(r"\s", "", m.group(1)))
    return None


def save_image_bytes(data: bytes, dest: str | Path) -> dict[str, Any]:
    from PIL import Image

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(data))
    fmt = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}.get(dest.suffix.lower().lstrip("."), "PNG")
    save = img.convert("RGB") if fmt == "JPEG" and img.mode not in ("RGB", "L") else img
    save.save(str(dest), format=fmt, quality=92)
    return {"bytes": dest.stat().st_size, "size": [img.width, img.height]}


async def generate_image(
    dest: str | Path,
    prompt: str,
    *,
    base_url: str,
    headers: dict[str, str],
    model: str = "",
    size: str = "1024x1024",
    timeout: float = 120.0,
    style_hint: str = "",
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the configured gateway image endpoint and save the result."""
    import httpx

    if not base_url:
        raise ValueError("image generation endpoint is not configured")
    full_prompt = photoreal_directive(prompt, style_hint)
    url = base_url if base_url.startswith("http") else f"{base_url.rstrip('/')}/images/generations"
    body: dict[str, Any] = {"prompt": full_prompt, "n": 1, "size": size}
    if model:
        body["model"] = model
    body.update(extra_body or {})
    req_headers = {"Content-Type": "application/json", **headers}
    async with httpx.AsyncClient(timeout=timeout, headers=req_headers, follow_redirects=True) as client:
        resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            detail = resp.text[:300]
            raise ConnectionError(f"gateway image endpoint returned HTTP {resp.status_code}: {detail}")
        data = resp.json()
    result = decode_image_payload(data)
    if result is None:
        raise ValueError(
            "could not find an image in the gateway response (accepted shapes: "
            "data[0].b64_json, data[0].url, image_base64, or a data-URL in text)"
        )
    if isinstance(result, str):  # a URL: download it
        img_resp = await client.get(result)
        if img_resp.status_code >= 400:
            raise ConnectionError(f"could not download the generated image (HTTP {img_resp.status_code})")
        result = img_resp.content
    meta = save_image_bytes(result, dest)
    meta.update({"endpoint": url, "model": model or "(gateway default)"})
    return meta
