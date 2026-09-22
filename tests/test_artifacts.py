"""Artifact tools: PDF/DOCX/XLSX/PPTX/CSV generation, synthetic data, charts,
doc_read verification, and artifact-evidence in the finish gate.

Skipped cleanly when the 'artifacts' extra is not installed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from wotan.agent.session import AgentSession
from wotan.artifacts.readers import read_text, sniff_header
from wotan.artifacts.synthetic import generate_rows
from wotan.config import parse_config
from wotan.editing.engine import EditEngine
from wotan.tools import ARTIFACT_TOOL_NAMES, ToolContext, ToolRegistry
from wotan.tools.artifact_tools import (_data_chart, _data_csv, _data_synthetic, _doc_docx,
                                        _doc_pdf, _doc_pptx, _doc_read, _doc_xlsx)

HAS_LIBS = all(importlib.util.find_spec(m) for m in ("docx", "openpyxl", "pptx", "reportlab", "PIL", "faker", "pypdf"))

pytestmark = pytest.mark.skipif(not HAS_LIBS, reason="artifacts extra not installed")


def make_ctx(ws: Path) -> ToolContext:
    from wotan.editing.checkpoints import CheckpointStore

    return ToolContext(workspace=ws, edit_engine=EditEngine(ws, checkpoints=None), session_id="t")


CONTRACT_MD = """# Contrato de Venda de Terreno

**Vendedor:** JOAO DA SILVA. **Comprador:** MARIA SOUZA.

## Cláusula 1 - Objeto
Venda do terreno matrícula 12345, área de 360m2, situado à Rua das Acácias, 100.

| Item | Descrição | Valor |
|------|-----------|-------|
| 1 | Terreno | R$ 150.000,00 |

> Modelo gerado automaticamente - não substitui assessoria jurídica.
"""


async def test_doc_pdf_generates_and_verifies(ws: Path):
    ctx = make_ctx(ws)
    out = await _doc_pdf(ctx, {"path": "contrato.pdf", "content_md": CONTRACT_MD, "title": "Contrato"})
    assert out["status"] == "ok", out
    assert out["pages"] >= 1
    assert (ws / "contrato.pdf").stat().st_size > 1000
    assert sniff_header(ws / "contrato.pdf") == "ok"
    text = read_text(ws / "contrato.pdf")
    assert "Contrato de Venda" in text["text"]
    assert "Cláusula 1" in text["text"]  # diacritics survive


async def test_doc_pdf_embeds_image(ws: Path):
    from PIL import Image

    Image.new("RGB", (400, 200), (30, 60, 90)).save(ws / "mapa.png")
    ctx = make_ctx(ws)
    md = CONTRACT_MD + "\n\n![mapa](mapa.png)\n"
    out = await _doc_pdf(ctx, {"path": "com_mapa.pdf", "content_md": md})
    assert out["status"] == "ok", out


async def test_doc_pdf_rejects_path_escape(ws: Path):
    ctx = make_ctx(ws)
    out = await _doc_pdf(ctx, {"path": "../fora.pdf", "content_md": CONTRACT_MD})
    assert out["status"] == "error"
    assert "escapes the workspace" in out["error"]


async def test_doc_docx_roundtrip(ws: Path):
    ctx = make_ctx(ws)
    out = await _doc_docx(ctx, {"path": "contrato.docx", "content_md": CONTRACT_MD, "title": "Contrato"})
    assert out["status"] == "ok", out
    assert sniff_header(ws / "contrato.docx") == "ok"
    text = read_text(ws / "contrato.docx")
    assert "MARIA SOUZA" in text["text"]
    assert text["tables"] == 1


async def test_doc_xlsx_types_and_readback(ws: Path):
    ctx = make_ctx(ws)
    sheets = [{"name": "Parcelas", "rows": [["Parcela", "Valor", "Vencimento"], ["1", "50000.5", "2026-10-10"], ["2", "50000", "2026-11-10"]], "autofilter": True}]
    out = await _doc_xlsx(ctx, {"path": "dados.xlsx", "sheets": sheets})
    assert out["status"] == "ok", out
    assert out["rows"] == 3
    data = read_text(ws / "dados.xlsx")
    assert "Parcela" in data["text"]


async def test_doc_pptx_slides(ws: Path):
    ctx = make_ctx(ws)
    slides = [
        {"layout": "title", "title": "Venda do Terreno", "subtitle": "Resumo"},
        {"layout": "bullets", "title": "Condições", "bullets": ["Entrada 20%", "24 parcelas"]},
        {"layout": "quote", "text": "A terra não se compra, se conquista", "author": "Anônimo"},
    ]
    out = await _doc_pptx(ctx, {"path": "deck.pptx", "slides": slides, "title": "Terreno"})
    assert out["status"] == "ok", out
    data = read_text(ws / "deck.pptx")
    assert data["slide_count"] == 3
    assert "Condições" in data["text"]


async def test_doc_pptx_missing_image_is_clean_error(ws: Path):
    ctx = make_ctx(ws)
    out = await _doc_pptx(ctx, {"path": "deck2.pptx", "slides": [{"layout": "image", "image_path": "nao_existe.png"}]})
    assert out["status"] == "error"
    assert "not found" in out["error"]


async def test_data_csv_and_read(ws: Path):
    ctx = make_ctx(ws)
    out = await _data_csv(ctx, {"path": "clientes.csv", "rows": [["nome", "valor"], ["Ana", 120], ["Bruno", 340]], "delimiter": ";"})
    assert out["status"] == "ok", out
    text = (ws / "clientes.csv").read_text(encoding="utf-8")
    assert "nome;valor" in text
    read = read_text(ws / "clientes.csv")
    assert read["delimiter"] == ";"


async def test_data_synthetic_deterministic_and_cpf_valid(ws: Path):
    def cpf_ok(cpf: str) -> bool:
        d = [int(c) for c in cpf if c.isdigit()]
        d1 = (sum(a * b for a, b in zip(d[:9], range(10, 1, -1))) * 10) % 11 % 10
        d2 = (sum(a * b for a, b in zip(d[:10], range(11, 1, -1))) * 10) % 11 % 10
        return d1 == d[9] and d2 == d[10]

    ctx = make_ctx(ws)
    schema = {"nome": "name", "cpf": "cpf", "salario": {"type": "money", "min": 1500, "max": 9000}, "uf": {"type": "choice", "choices": ["SP", "RJ"]}}
    out = await _data_synthetic(ctx, {"schema": schema, "rows": 30, "seed": 99, "format": "csv", "path": "pessoas.csv"})
    assert out["status"] == "ok", out
    assert out["rows_generated"] == 30
    rows = generate_rows(schema, 30, seed=99)
    for row in rows[1:]:
        assert cpf_ok(row[1]), row
    again = generate_rows(schema, 30, seed=99)
    assert rows == again  # deterministic


async def test_data_synthetic_unknown_type_is_clean_error(ws: Path):
    ctx = make_ctx(ws)
    out = await _data_synthetic(ctx, {"schema": {"x": "não_existe"}, "rows": 5})
    assert out["status"] == "error"
    assert "unknown field type" in out["error"]


async def test_data_chart_png(ws: Path):
    from PIL import Image

    ctx = make_ctx(ws)
    out = await _data_chart(ctx, {"path": "vendas.png", "kind": "bar", "labels": ["Jan", "Fev", "Mar"], "series": [{"name": "Vendas", "values": [120, 90, 180]}], "title": "Vendas"})
    assert out["status"] == "ok", out
    img = Image.open(ws / "vendas.png")
    assert img.size == (900, 560)
    colors = img.convert("RGB").getcolors(maxcolors=100000)
    assert len(colors) > 10  # actually drew something


async def test_img_transform_watermark_resize(ws: Path):
    from PIL import Image

    Image.new("RGB", (800, 600), (200, 200, 210)).save(ws / "foto.jpg")
    ctx = make_ctx(ws)
    out = ctx
    from wotan.tools.artifact_tools import _img_transform

    res = await _img_transform(out, {"src": "foto.jpg", "ops": [{"op": "resize", "width": 400}, {"op": "watermark", "text": "RASCUNHO"}]})
    assert res["status"] == "ok", res
    assert res["size"] == [400, 300]
    img = Image.open(ws / "foto_edit.jpg")
    assert img.size == (400, 300)


async def test_doc_read_roundtrip_and_missing(ws: Path):
    ctx = make_ctx(ws)
    await _doc_docx(ctx, {"path": "doc.docx", "content_md": "# Titulo\n\nTexto com acentuação."})
    out = await _doc_read(ctx, {"path": "doc.docx"})
    assert out["status"] == "ok"
    assert "Titulo" in out["text"]
    missing = await _doc_read(ctx, {"path": "nope.pdf"})
    assert missing["status"] == "error"


async def test_doc_read_detects_corrupt_file(ws: Path):
    (ws / "quebrado.pdf").write_bytes(b"nao e um pdf")
    ctx = make_ctx(ws)
    out = await _doc_read(ctx, {"path": "quebrado.pdf"})
    assert out["status"] == "error"
    assert "not a PDF" in out["error"]


def test_artifact_tools_registered():
    reg = ToolRegistry()
    from wotan.tools import build_registry

    reg = build_registry()
    for name in ARTIFACT_TOOL_NAMES:
        tool = reg.get(name)
        assert tool is not None, name
        assert tool.parameters.get("properties"), name


# ---------------------------------------------------------------------------
# finish gate: artifact evidence
# ---------------------------------------------------------------------------

def test_gate_accepts_artifact_evidence(ws: Path, db, tmp_path: Path):
    from wotan.agent.verification import VerificationGate
    from wotan.agent.execution_log import ExecutionLog

    (ws / "relatorio.pdf").write_bytes(b"%PDF-1.4 fake but header-sniffable and >200 bytes" + b"x" * 300)
    gate = VerificationGate(parse_config({}).verification, ExecutionLog(db))
    gate.start_task("t1")
    checks = gate.check_artifacts([{"path": "relatorio.pdf"}], ws)
    assert all(c.ok for c in checks), checks
    verdict = gate.evaluate_finish(
        {"summary": "gerado", "acceptance_criteria": [{"criterion": "pdf existe", "status": "passed", "evidence": "doc_read ok"}], "commands": [], "artifacts": [{"path": "relatorio.pdf"}]},
        artifact_checks=checks,
    )
    assert verdict["finished"] is True, verdict


def test_gate_refuses_missing_artifact(ws: Path, db):
    from wotan.agent.verification import VerificationGate
    from wotan.agent.execution_log import ExecutionLog

    gate = VerificationGate(parse_config({}).verification, ExecutionLog(db))
    gate.start_task("t2")
    checks = gate.check_artifacts([{"path": "fantasma.pdf"}], ws)
    assert not any(c.ok for c in checks)
    verdict = gate.evaluate_finish(
        {"summary": "mentei", "acceptance_criteria": [], "commands": [], "artifacts": [{"path": "fantasma.pdf"}]},
        artifact_checks=checks,
    )
    assert verdict["finished"] is False
    assert "never created" in verdict["error"]["why"]


def test_gate_refuses_when_artifact_evidence_claimed_but_absent(db):
    from wotan.agent.verification import VerificationGate
    from wotan.agent.execution_log import ExecutionLog

    gate = VerificationGate(parse_config({}).verification, ExecutionLog(db))
    gate.start_task("t3")
    verdict = gate.evaluate_finish({
        "summary": "s", "acceptance_criteria": [], "commands": [], "artifacts": [],
    })
    assert verdict["finished"] is False


async def test_end_to_end_turn_generates_pdf_and_finishes(ws: Path, db):
    collected: list[dict] = []

    async def emit(e: dict) -> None:
        collected.append(e)

    cfg = parse_config({"permissions": {"default_mode": "autonomous"}, "verification": {"on_stop_hook": False}})
    session = AgentSession(cfg, ws, db, emit)

    from tests.test_agent_loop import FakeProvider, FakeRegistry
    from wotan.providers.base import ChatResult, ToolCall, Usage

    pdf_call = ChatResult(tool_calls=[ToolCall(id="c0", name="doc_pdf", arguments={"path": "auto.pdf", "content_md": "# Relatorio\n\nConteudo **teste**."})], usage=Usage(5, 5))
    read_call = ChatResult(tool_calls=[ToolCall(id="c1", name="doc_read", arguments={"path": "auto.pdf"})], usage=Usage(5, 5))
    finish_call = ChatResult(tool_calls=[ToolCall(id="c2", name="finish_task", arguments={
        "summary": "pdf gerado",
        "acceptance_criteria": [{"criterion": "pdf valido", "status": "passed", "evidence": "doc_read ok"}],
        "commands": [],
        "artifacts": [{"path": "auto.pdf"}],
    })], usage=Usage(5, 5))
    done = ChatResult(text="pronto", usage=Usage(1, 1))
    session.registry = FakeRegistry(FakeProvider([pdf_call, read_call, finish_call, done]))  # type: ignore[assignment]
    result = await session.run_turn("gere um pdf")
    assert result["status"] in ("done", "finished"), result
    assert (ws / "auto.pdf").is_file()
    verdicts = [e for e in collected if e["type"] == "finish_verdict"]
    assert verdicts and verdicts[-1].get("finished") is True
    assert any(e["type"] == "artifact_check" and e["ok"] for e in collected)


# ---------------------------------------------------------------------------
# img_satellite: mosaic stitching + SSRF guard (local tile server)
# ---------------------------------------------------------------------------

class _TileServer:
    """Tiny HTTP server serving synthetic 256x256 satellite-like tiles."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from PIL import Image, ImageDraw
        import io

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parts = [p for p in self.path.split("/") if p]
                try:
                    z, x, y = (int(parts[-3]), int(parts[-2]), int(parts[-1]))
                except (ValueError, IndexError):
                    self.send_response(404)
                    self.end_headers()
                    return
                img = Image.new("RGB", (256, 256), (40 + (x % 8) * 20, 80 + (y % 8) * 15, 40))
                d = ImageDraw.Draw(img)
                d.rectangle([32, 32, 224, 224], outline=(240, 240, 240), width=3)
                d.text((60, 110), f"{z}/{x}/{y}", fill=(255, 255, 0))
                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(buf.getvalue())

            def log_message(self, *args):  # silence
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/tile/{{z}}/{{x}}/{{y}}"

    def stop(self) -> None:
        self.server.shutdown()


def test_satellite_mosaic_stitches_tiles(ws: Path):
    from PIL import Image

    from wotan.artifacts.images import satellite_mosaic

    srv = _TileServer()
    try:
        meta = satellite_mosaic(
            ws / "sat.jpg", lat=-23.55, lon=-46.63, zoom=18, tiles=1,
            base_url=srv.url, allowed_hosts=["127.0.0.1"], timeout=5.0,
            attribution="Test provider",
        )
        assert meta["tiles_fetched"] == 4 and meta["tiles_failed"] == 0
        img = Image.open(ws / "sat.jpg")
        assert img.size == (512, 512)
    finally:
        srv.stop()


def test_satellite_blocks_non_allowlisted_host(ws: Path):
    from wotan.artifacts.images import satellite_mosaic

    with pytest.raises(PermissionError):
        satellite_mosaic(
            ws / "sat2.jpg", lat=-23.55, lon=-46.63, zoom=18, tiles=1,
            base_url="http://evil.example.com/tile/{z}/{x}/{y}",
            allowed_hosts=["server.arcgisonline.com"], timeout=3.0,
        )


async def test_img_satellite_tool_requires_config(ws: Path):
    from wotan.tools.artifact_tools import _img_satellite

    ctx = make_ctx(ws)
    out = await _img_satellite(ctx, {"lat": -23.55, "lon": -46.63, "path": "s.jpg"})
    assert out["status"] == "error"
    assert "no satellite provider configured" in out["error"]
