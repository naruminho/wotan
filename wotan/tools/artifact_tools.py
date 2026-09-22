"""Artifact tool handlers: document / data / image generation for the agent.

Handlers map 1:1 to the tools registered in :mod:`wotan.tools`:

- doc_pdf / doc_docx / doc_xlsx / doc_pptx / doc_read  (documents)
- data_csv / data_synthetic / data_chart               (data)
- img_transform / img_satellite                        (images)

Every handler: resolves paths INSIDE the workspace (same policy as the fs
tools), degrades gracefully when an optional library is missing (ERROR / WHY /
HOW TO FIX dict, never an exception), and returns metadata the verification
gate can check (see ``wotan.artifacts.readers.sniff_header``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import _err, _ok
from ..artifacts import library_missing
from ..artifacts.readers import read_text, sniff_header
from ..security import is_sensitive_path


def _resolve(ctx, path: str) -> Path:  # noqa: ANN001
    """Same workspace-containment policy as the fs tools."""
    from . import _inside

    return _inside(ctx, path)


def _resolve_safe(ctx, path: str) -> tuple[Path | None, dict[str, Any] | None]:  # noqa: ANN001
    """Resolve or produce a clean ERROR/WHY/HOW TO FIX payload."""
    try:
        return _resolve(ctx, path), None
    except PermissionError as exc:
        return None, _err(str(exc), "path escapes the workspace", "use a path inside the workspace root")


def _require_mod(exc: ModuleNotFoundError) -> dict[str, Any]:
    return library_missing(_root_module(exc))


def _root_module(exc: ModuleNotFoundError) -> str:
    return exc.name.split(".")[0] if exc.name else "module"


def _not_found(name: str, ctx_path: str) -> dict[str, Any]:
    return _err(
        f"source file not found: {name}",
        "the referenced file does not exist in the workspace",
        f"check the path with fs_list (relative to the workspace root) - got {ctx_path!r}",
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

async def _doc_pdf(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    content = args.get("content_md") or args.get("content") or ""
    if not path or not content.strip():
        return _err("path and content_md are required", "nothing to render", "pass a .pdf path and the markdown-lite content")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".pdf":
        dest = dest.with_suffix(".pdf")
    if is_sensitive_path(dest):
        return _err("refusing to write a sensitive-looking file", "the path looks like a credential store", "choose a normal document name")
    try:
        from ..artifacts.pdf import write_pdf
    except ModuleNotFoundError as exc:  # pragma: no cover - import-time only
        return _require_mod(exc)
    try:
        meta = write_pdf(
            dest,
            str(content),
            title=str(args.get("title") or ""),
            author=str(args.get("author") or ""),
            subject=str(args.get("subject") or ""),
            page_size=str(args.get("page_size") or "a4"),
            page_numbers=bool(args.get("page_numbers", True)),
            base_dir=ctx.workspace,
            margin_cm=float(args.get("margin_cm", 2.0)),
        )
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    except Exception as exc:
        return _err(f"PDF generation failed: {type(exc).__name__}: {exc}", "the renderer rejected the content", "simplify the markdown (tables need |---| separators; images must exist) and retry")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "the writer produced a corrupt file", "retry with simpler content and report this as a bug")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta, verified="header + text extraction ok")


async def _doc_docx(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    content = args.get("content_md") or args.get("content") or ""
    if not path or not content.strip():
        return _err("path and content_md are required", "nothing to render", "pass a .docx path and the markdown-lite content")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".docx":
        dest = dest.with_suffix(".docx")
    try:
        from ..artifacts.docx import write_docx
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = write_docx(
            dest,
            str(content),
            title=str(args.get("title") or ""),
            author=str(args.get("author") or ""),
            subject=str(args.get("subject") or ""),
            base_dir=ctx.workspace,
        )
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    except Exception as exc:
        return _err(f"DOCX generation failed: {type(exc).__name__}: {exc}", "the renderer rejected the content", "simplify the markdown and retry")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "the writer produced a corrupt file", "retry and report this as a bug")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _doc_xlsx(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    sheets = args.get("sheets") or []
    if not path:
        return _err("path is required", "no destination", "pass a .xlsx path")
    if not sheets or not isinstance(sheets, list):
        return _err("sheets is required", "workbook needs at least one sheet", 'pass sheets=[{"name": "Dados", "rows": [["col"], [1]]}]')
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() not in (".xlsx", ".xlsm"):
        dest = dest.with_suffix(".xlsx")
    try:
        from ..artifacts.common import rows_from_value
        from ..artifacts.xlsx import write_xlsx
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    norm: list[dict[str, Any]] = []
    try:
        for s in sheets:
            if not isinstance(s, dict):
                raise ValueError("each sheet must be an object with name and rows")
            norm.append({**s, "rows": rows_from_value(s.get("rows") or [])})
        meta = write_xlsx(dest, norm)
    except (ValueError, TypeError) as exc:
        return _err(f"invalid sheets payload: {exc}", "rows must be a 2D array (or a {header, rows} / list-of-objects shape)", "example: sheets=[{\"name\": \"Vendas\", \"rows\": [[\"mes\", \"valor\"], [\"Jan\", 100]]}]")
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    except Exception as exc:
        return _err(f"XLSX generation failed: {type(exc).__name__}: {exc}", "the workbook could not be written", "check sheet names (<=31 chars) and cell values")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "the writer produced a corrupt file", "retry and report this as a bug")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _doc_pptx(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    slides = args.get("slides") or []
    if not path:
        return _err("path is required", "no destination", "pass a .pptx path")
    if not slides or not isinstance(slides, list):
        return _err("slides is required", "a deck needs at least one slide", 'pass slides=[{"layout": "title", "title": "..."}, {"layout": "bullets", "title": "...", "bullets": ["..."]}]')
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".pptx":
        dest = dest.with_suffix(".pptx")
    # resolve slide image paths relative to the workspace
    for s in slides:
        if isinstance(s, dict) and s.get("image_path") and not Path(str(s["image_path"])).is_absolute():
            s["image_path"] = str(_resolve(ctx, str(s["image_path"])))
    try:
        from ..artifacts.pptx import write_pptx
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = write_pptx(
            dest,
            slides,
            title=str(args.get("title") or ""),
            author=str(args.get("author") or ""),
            aspect=str(args.get("aspect") or "16:9"),
            theme=str(args.get("theme") or "executive"),
            footer_title=str(args.get("footer_title") or ""),
        )
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    except FileNotFoundError as exc:
        return _err(f"slide image not found: {exc}", "an image_path does not exist", "check the path with fs_list or remove the image")
    except Exception as exc:
        return _err(f"PPTX generation failed: {type(exc).__name__}: {exc}", "the deck could not be written", "check slide layouts (title|section|bullets|two_content|image|quote) and retry")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "the writer produced a corrupt file", "retry and report this as a bug")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _doc_read(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    if not path:
        return _err("path is required", "nothing to read", "pass the artifact path")
    target, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if not target.is_file():
        return _not_found(path, str(target))
    probe = sniff_header(target)
    if probe != "ok":
        return _err(f"file failed verification: {probe}", "the file is missing, empty or corrupt", "regenerate the artifact or check the source")
    max_chars = int(args.get("max_chars", 8000))
    try:
        data = read_text(target, max_chars=max_chars)
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    except Exception as exc:
        return _err(f"could not parse {target.suffix}: {type(exc).__name__}: {exc}", "unsupported or corrupt format", "for binary formats the generating library must be installed")
    data.pop("path", None)
    return _ok(path=str(target.relative_to(ctx.workspace)).replace("\\", "/"), **data)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

async def _data_csv(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    rows_raw = args.get("rows")
    if not path:
        return _err("path is required", "no destination", "pass a .csv path")
    if rows_raw is None:
        return _err("rows is required", "nothing to write", "pass rows=[[...header...], [ ... ]]")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".csv":
        dest = dest.with_suffix(".csv")
    from ..artifacts.common import rows_from_value
    from ..artifacts.csvw import write_csv

    try:
        rows = rows_from_value(rows_raw)
        meta = write_csv(
            dest,
            rows,
            delimiter=str(args.get("delimiter") or ","),
            encoding=str(args.get("encoding") or "utf-8"),
            header=bool(args.get("header", True)),
        )
    except (ValueError, TypeError) as exc:
        return _err(f"invalid rows payload: {exc}", "rows must be a 2D array or a {header, rows} object", "example: rows=[[\"nome\", \"valor\"], [\"Ana\", 10]]")
    except LookupError as exc:
        return _err(f"unknown encoding: {exc}", "the encoding name is not valid", "use utf-8, utf-8-sig (Excel-friendly) or latin-1")
    from ..artifacts.csvw import markdown_table

    preview = markdown_table(rows[:6])
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta, preview=preview)


async def _data_synthetic(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    schema = args.get("schema")
    rows_n = int(args.get("rows", 20))
    fmt = str(args.get("format") or "csv").lower()
    path = str(args.get("path") or "").strip()
    seed = int(args.get("seed", 42))
    locale = str(args.get("locale") or "pt_BR")
    if not isinstance(schema, dict) or not schema:
        return _err("schema is required", "a generator needs field definitions", 'example: schema={"nome": "name", "cpf": "cpf", "salario": {"type": "money", "min": 1500, "max": 20000}}')
    if rows_n < 1:
        return _err("rows must be >= 1", "nothing to generate", "pass rows=10 or more")
    try:
        from ..artifacts.synthetic import generate_rows, summarize
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        rows = generate_rows(schema, rows_n, seed=seed, locale=locale)
    except (ValueError, TypeError) as exc:
        return _err(f"invalid schema: {exc}", "field types are validated", "supported types: name, email, cpf, cnpj, phone, address, city, date, int, number, money, bool, choice, uuid, word, sentence, ...")
    dest_rel = ""
    if path:
        dest, perr = _resolve_safe(ctx, path)
        if perr is not None:
            return perr
        try:
            if fmt in ("csv", ""):
                from ..artifacts.csvw import write_csv

                meta = write_csv(dest if dest.suffix == ".csv" else dest.with_suffix(".csv"), rows,
                                 delimiter=str(args.get("delimiter") or ";"), encoding=str(args.get("encoding") or "utf-8-sig"))
                dest_rel = str(dest.with_suffix(".csv").relative_to(ctx.workspace)).replace("\\", "/")
            elif fmt in ("xlsx", "excel"):
                from ..artifacts.xlsx import write_xlsx

                target = dest if dest.suffix in (".xlsx", ".xlsm") else dest.with_suffix(".xlsx")
                meta = write_xlsx(target, [{"name": str(args.get("sheet_name") or "dados"), "rows": rows, "autofilter": True}])
                dest_rel = str(target.relative_to(ctx.workspace)).replace("\\", "/")
            elif fmt == "json":
                target = dest if dest.suffix == ".json" else dest.with_suffix(".json")
                header = rows[0]
                records = [dict(zip(header, r)) for r in rows[1:]]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                meta = {"bytes": target.stat().st_size, "rows": len(records)}
                dest_rel = str(target.relative_to(ctx.workspace)).replace("\\", "/")
            elif fmt in ("md", "markdown"):
                from ..artifacts.csvw import markdown_table

                target = dest if dest.suffix == ".md" else dest.with_suffix(".md")
                target.parent.mkdir(parents=True, exist_ok=True)
                table = markdown_table(rows[: min(rows_n, 50) + 1], max_cell=40)
                target.write_text(f"# Dados sinteticos (seed={seed}, locale={locale})\n\n{table}\n", encoding="utf-8")
                meta = {"bytes": target.stat().st_size, "rows": len(rows) - 1}
                dest_rel = str(target.relative_to(ctx.workspace)).replace("\\", "/")
            else:
                return _err(f"unsupported format {fmt!r}", "format must be csv | xlsx | json | md", "use format='xlsx' for spreadsheets with types and formulas")
        except (ValueError, OSError) as exc:
            return _err(f"could not write {fmt}: {exc}", "the destination or payload is invalid", "check the path and the row payload")
    preview_rows = rows[:5]
    from ..artifacts.csvw import markdown_table

    return _ok(
        rows_generated=len(rows) - 1,
        seed=seed,
        locale=locale,
        file=dest_rel or None,
        preview=markdown_table(preview_rows),
        profile=summarize(rows),
    )


async def _data_chart(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    kind = str(args.get("kind") or "bar")
    series = args.get("series") or []
    if not path:
        return _err("path is required", "no destination", "pass a .png path")
    if not series:
        return _err("series is required", "a chart needs values", 'example: series=[{"name": "Vendas", "values": [10, 20, 30]}]')
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".png":
        dest = dest.with_suffix(".png")
    try:
        from ..artifacts.charts import write_chart
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = write_chart(
            dest,
            kind,
            labels=[str(l) for l in (args.get("labels") or [])],
            series=series,
            title=str(args.get("title") or ""),
            width=int(args.get("width", 900)),
            height=int(args.get("height", 560)),
            palette=str(args.get("palette") or "blue"),
            bins=int(args.get("bins", 10)),
            x_label=str(args.get("x_label") or ""),
            y_label=str(args.get("y_label") or ""),
        )
    except (ValueError, TypeError) as exc:
        return _err(f"invalid chart payload: {exc}", "kinds: bar, hbar, line, area, pie, donut, scatter, histogram", "example: {kind: 'bar', labels: ['Jan'], series: [{name: 'Vendas', values: [10]}]}")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "the writer produced a corrupt file", "retry and report this as a bug")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

async def _img_transform(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    src = str(args.get("src") or "").strip()
    dest_arg = str(args.get("dest") or "").strip()
    ops = args.get("ops") or []
    if not src:
        return _err("src is required", "nothing to transform", "pass the source image path in the workspace")
    src_p, perr = _resolve_safe(ctx, src)
    if perr is not None:
        return perr
    if not src_p.is_file():
        return _not_found(src, str(src_p))
    dest_p = _resolve(ctx, dest_arg) if dest_arg else src_p.with_name(f"{src_p.stem}_edit{src_p.suffix}")
    try:
        from ..artifacts.images import transform_image
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = transform_image(src_p, dest_p, ops)
    except (ValueError, TypeError) as exc:
        return _err(f"invalid image op: {exc}", "ops: resize, crop, rotate, grayscale, flip_h, flip_v, brightness, contrast, watermark, border", "example: ops=[{op: 'resize', width: 800}, {op: 'watermark', text: 'Rascunho'}]")
    return _ok(path=str(dest_p.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _img_satellite(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    lat = args.get("lat")
    lon = args.get("lon")
    path = str(args.get("path") or "").strip()
    if lat is None or lon is None or not path:
        return _err("lat, lon and path are required", "incomplete location", "example: {lat: -23.5505, lon: -46.6333, path: 'terreno/satelite.jpg', zoom: 18}")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        dest = dest.with_suffix(".jpg")
    extra = getattr(ctx, "extra", None) or {}
    art_cfg = extra.get("artifacts_config") if isinstance(extra, dict) else getattr(extra, "artifacts_config", None)
    sat_cfg = getattr(art_cfg, "satellite", None) if art_cfg is not None else None
    base_url = str(args.get("provider_url") or (getattr(sat_cfg, "base_url", "") or ""))
    allowed = list(getattr(sat_cfg, "allowed_hosts", []) or [])
    headers = dict(getattr(sat_cfg, "headers", {}) or {})
    timeout = float(getattr(sat_cfg, "timeout_seconds", 20.0))
    attribution = str(args.get("attribution") or getattr(sat_cfg, "attribution", "") or "Imagens de satelite")
    if not base_url:
        return _err(
            "no satellite provider configured",
            "artifact satellite imagery needs a tile URL template in config.yaml (artifacts.satellite.base_url)",
            "add artifacts.satellite.base_url with {z}/{x}/{y} to the config, or attach a local image instead",
        )
    try:
        from ..artifacts.images import satellite_mosaic
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = satellite_mosaic(
            dest,
            float(lat),
            float(lon),
            zoom=int(args.get("zoom", 18)),
            tiles=int(args.get("tiles", 2)),
            base_url=base_url,
            allowed_hosts=allowed,
            headers=headers,
            timeout=timeout,
            attribution=attribution,
        )
    except PermissionError as exc:
        return _err(str(exc), "SSRF guard: the tile host is not allow-listed", f"add the host to artifacts.satellite.allowed_hosts in config.yaml (currently: {allowed})")
    except ValueError as exc:
        return _err(f"invalid request: {exc}", "lat/lon/zoom out of range", "lat in [-85, 85], lon in [-180, 180], zoom 1-21, tiles 1-3")
    except ConnectionError as exc:
        return _err(str(exc), "the tile provider is unreachable", "check the network / provider URL, lower tiles, or ask the user for a local image to embed")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


# ---------------------------------------------------------------------------
# LLM-generated images (multimodal gateway) + scanned-document simulation
# ---------------------------------------------------------------------------

async def _img_llm(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    prompt = str(args.get("prompt") or "").strip()
    path = str(args.get("path") or "").strip()
    if not prompt or not path:
        return _err("prompt and path are required", "nothing to generate",
                    "example: {prompt: 'foto aerea do lote em dia ensolarado', path: 'terreno/aerea.png'}")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        dest = dest.with_suffix(".png")
    generate = getattr(ctx, "extra", {}).get("imagegen") if isinstance(getattr(ctx, "extra", None), dict) else None
    if generate is None:
        return _err("image generation is not available in this context", "no imagegen callable attached",
                    "run inside an agent session with artifacts.image_generation configured")
    try:
        meta = await generate(prompt, dest, args)
    except (ValueError, ConnectionError) as exc:
        return _err(f"image generation failed: {exc}", "the gateway call or the response format failed",
                    "check artifacts.image_generation in config.yaml (provider/model/endpoint) and run 'wotan doctor'")
    except Exception as exc:
        return _err(f"image generation crashed: {type(exc).__name__}: {exc}", "unexpected error",
                    "check the gateway endpoint; see read_app_logs for the full trace")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "corrupt image", "retry the generation")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _doc_scan_image(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    content = args.get("content_md") or args.get("content") or ""
    form = args.get("form") or None
    if not path or (not str(content).strip() and not form):
        return _err("path and (content_md or form) are required", "nothing to render",
                    "pass the document markdown-lite and/or a form spec {fields, checkboxes, signature, stamp_text}")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        dest = dest.with_suffix(".png")
    try:
        from ..artifacts.scandoc import render_scanned_image
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = render_scanned_image(
            dest, str(content), form=form,
            mode=str(args.get("mode") or "scan"), seed=int(args.get("seed", 7)),
            dpi=int(args.get("dpi", 150)),
        )
    except (ValueError, TypeError) as exc:
        return _err(f"invalid scan spec: {exc}", "mode must be scan | photo | photocopy; form needs fields/checkboxes/signature/stamp_text",
                    "example: {mode: 'scan', form: {fields: [{label: 'Nome', value: 'Maria'}], signature: true, stamp_text: 'RECEBIDO | SETOR 2'}}")
    except Exception as exc:
        return _err(f"scan simulation failed: {type(exc).__name__}: {exc}", "renderer error", "simplify the content and retry")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "corrupt image", "retry")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)


async def _doc_scan_pdf(ctx, args: dict[str, Any]) -> Any:  # noqa: ANN001
    path = str(args.get("path") or "").strip()
    content = args.get("content_md") or args.get("content") or ""
    form = args.get("form") or None
    form_pages = args.get("form_pages") or None
    if not path or (not str(content).strip() and not form and not form_pages):
        return _err("path and content are required", "nothing to render",
                    "pass content_md (use <<<PAGEBREAK>>> between pages) and/or form / form_pages")
    dest, perr = _resolve_safe(ctx, path)
    if perr is not None:
        return perr
    if dest.suffix.lower() != ".pdf":
        dest = dest.with_suffix(".pdf")
    try:
        from ..artifacts.scandoc import write_scanned_pdf
    except ModuleNotFoundError as exc:
        return _require_mod(exc)
    try:
        meta = write_scanned_pdf(
            dest, str(content), form=form, form_pages=form_pages,
            mode=str(args.get("mode") or "scan"), seed=int(args.get("seed", 7)),
            dpi=int(args.get("dpi", 150)), title=str(args.get("title") or ""),
        )
    except (ValueError, TypeError) as exc:
        return _err(f"invalid scan spec: {exc}", "form_pages is a list of {content_md?, fields?, checkboxes?, signature?, stamp_text?}",
                    "example: form_pages=[{content_md: '...'}, {fields: [{label: 'Setor', value: '2'}]}]")
    except Exception as exc:
        return _err(f"scan PDF failed: {type(exc).__name__}: {exc}", "renderer error", "simplify the content and retry")
    probe = sniff_header(dest)
    if probe != "ok":
        return _err(f"generated file failed verification: {probe}", "corrupt PDF", "retry")
    return _ok(path=str(dest.relative_to(ctx.workspace)).replace("\\", "/"), **meta)
