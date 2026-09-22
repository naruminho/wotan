"""Synthetic scanned documents (OCR fixtures) and LLM image generation tool.

Skipped cleanly when the 'artifacts' extra is not installed.
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("PIL", "reportlab")),
    reason="artifacts extra not installed",
)

from wotan.artifacts.readers import sniff_header
from wotan.artifacts.scandoc import render_scanned_image, write_scanned_pdf

DOC_MD = """# FORMULÁRIO DE CADASTRO

**Nome:** MARIA SOUZA

- [x] Termo aceito
- [ ] Newsletter

| Campo | Valor |
|-------|-------|
| UF | SP |
| CEP | 01310-100 |
"""

FORM = {
    "fields": [
        {"label": "Telefone", "value": "(11) 91234-5678"},
        {"label": "Protocolo", "value": "2026-0999"},
    ],
    "checkboxes": [{"label": "Dados conferidos", "checked": True}],
    "signature": True,
    "stamp_text": "RECEBIDO | SETOR 3",
}


@pytest.mark.parametrize("mode", ["scan", "photo", "photocopy"])
def test_render_modes_produce_valid_images(ws: Path, mode: str):
    from PIL import Image

    out = render_scanned_image(ws / f"scan_{mode}.png", DOC_MD, form=FORM, mode=mode, seed=11)
    assert out["bytes"] > 10_000
    assert sniff_header(ws / f"scan_{mode}.png") == "ok"
    img = Image.open(ws / f"scan_{mode}.png")
    assert img.size == (1240, 1753)


def test_scan_is_deterministic_under_seed(ws: Path):
    render_scanned_image(ws / "d1.png", DOC_MD, form=FORM, mode="scan", seed=42)
    render_scanned_image(ws / "d2.png", DOC_MD, form=FORM, mode="scan", seed=42)
    render_scanned_image(ws / "d3.png", DOC_MD, form=FORM, mode="scan", seed=43)
    assert (ws / "d1.png").read_bytes() == (ws / "d2.png").read_bytes()
    assert (ws / "d1.png").read_bytes() != (ws / "d3.png").read_bytes()


def test_scanned_pdf_multipage(ws: Path):
    out = write_scanned_pdf(
        ws / "scan.pdf",
        form_pages=[{"content_md": DOC_MD}, {"fields": [{"label": "Setor", "value": "2"}], "stamp_text": "ARQUIVADO"}],
        mode="scan", seed=5, title="Cadastro",
    )
    assert out["pages"] == 2
    assert sniff_header(ws / "scan.pdf") == "ok"


def test_scan_rejects_bad_mode(ws: Path):
    with pytest.raises(ValueError):
        render_scanned_image(ws / "x.png", DOC_MD, mode="holograma")
    with pytest.raises(ValueError):
        # write_scanned_pdf refuses a document with no content at all
        write_scanned_pdf(ws / "y.pdf")


async def test_doc_scan_tool_happy_and_error(ws: Path):
    from wotan.tools.artifact_tools import _doc_scan_image, _doc_scan_pdf
    from tests.test_artifacts import make_ctx

    ctx = make_ctx(ws)
    out = await _doc_scan_image(ctx, {"path": "fixtures/form.png", "content_md": DOC_MD, "form": FORM, "mode": "photo", "seed": 3})
    assert out["status"] == "ok", out
    bad = await _doc_scan_image(ctx, {"path": "fixtures/form2.png", "content_md": "texto", "mode": "holo"})
    assert bad["status"] == "error" and "scan | photo | photocopy" in bad["why"]
    empty = await _doc_scan_image(ctx, {"path": "fixtures/form3.png"})
    assert empty["status"] == "error" and "nothing to render" in empty["why"]
    pdf_out = await _doc_scan_pdf(ctx, {"path": "fixtures/form.pdf", "content_md": DOC_MD, "form": FORM})
    assert pdf_out["status"] == "ok", pdf_out


# ---------------------------------------------------------------------------
# img_llm: gateway image generation (mocked callable + payload decoding)
# ---------------------------------------------------------------------------

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


async def test_img_llm_without_config_is_clean_error(ws: Path):
    from wotan.tools.artifact_tools import _img_llm
    from tests.test_artifacts import make_ctx

    ctx = make_ctx(ws)
    out = await _img_llm(ctx, {"prompt": "foto", "path": "a.png"})
    assert out["status"] == "error"
    assert "imagegen" in out["why"] or "image generation is not available" in out["error"]


async def test_img_llm_with_callable(ws: Path):
    from wotan.tools.artifact_tools import _img_llm
    from tests.test_artifacts import make_ctx

    async def fake_gen(prompt, dest, args):
        (ws / "gerado.png").write_bytes(PNG_1PX)
        return {"bytes": len(PNG_1PX), "size": [1, 1], "model": "mock"}

    ctx = make_ctx(ws)
    ctx.extra["imagegen"] = fake_gen
    out = await _img_llm(ctx, {"prompt": "retrato de cliente", "path": "gerado.png"})
    assert out["status"] == "ok", out
    assert out["model"] == "mock"


async def test_img_llm_requires_prompt_and_path(ws: Path):
    from wotan.tools.artifact_tools import _img_llm
    from tests.test_artifacts import make_ctx

    ctx = make_ctx(ws)
    ctx.extra["imagegen"] = None
    out = await _img_llm(ctx, {"prompt": "", "path": ""})
    assert out["status"] == "error"


def test_photoreal_directive_people_and_graphics():
    from wotan.artifacts.llm_image import photoreal_directive

    p1 = photoreal_directive("Uma pessoa esperando na fila do banco")
    assert "textura de pele" in p1 and "fotografia realista" in p1
    p2 = photoreal_directive("Infográfico de vendas por região")
    assert "minimalista" in p2 and "néon" in p2 or "neon" in p2.lower()
    p3 = photoreal_directive("Paisagem de campo", style_hint="tons quentes")
    assert "tons quentes" in p3 and "textura de pele" not in p3
    assert photoreal_directive("sem extras") == "sem extras"


def test_decode_image_payload_shapes():
    from wotan.artifacts.llm_image import decode_image_payload

    b64 = base64.b64encode(PNG_1PX).decode()
    assert decode_image_payload({"data": [{"b64_json": b64}]}) == PNG_1PX
    assert decode_image_payload({"data": [{"url": "https://x/y.png"}]}) == "https://x/y.png"
    assert decode_image_payload({"image_base64": b64}) == PNG_1PX
    assert decode_image_payload({"text": f"data:image/png;base64,{b64}"}) == PNG_1PX
    assert decode_image_payload({"nope": 1}) is None


async def test_session_imagegen_not_configured(ws, db):
    from wotan.agent.session import AgentSession
    from wotan.config import parse_config

    async def emit(e: dict) -> None:
        pass

    session = AgentSession(parse_config({}), ws, db, emit)
    with pytest.raises(ValueError) as excinfo:
        await session._generate_image("prompt", ws / "x.png", {})
    assert "artifacts.image_generation" in str(excinfo.value)


def test_llm_image_endpoint_error_is_clean(ws, tmp_path):
    import httpx
    import pytest as _pytest

    from wotan.artifacts.llm_image import generate_image

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def run():
        client_transport = httpx.MockTransport(handler)
        import httpx as _h

        orig_client = _h.AsyncClient

        def factory(*a, **kw):
            kw["transport"] = client_transport
            return orig_client(*a, **kw)

        _h.AsyncClient = factory
        try:
            await generate_image(tmp_path / "out.png", "teste", base_url="http://gw.test/v1", headers={}, model="m")
        finally:
            _h.AsyncClient = orig_client

    import asyncio

    with _pytest.raises(ConnectionError):
        asyncio.run(run())
