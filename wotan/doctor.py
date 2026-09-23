"""``wotan doctor`` / ``wotan probe``: tune the configuration in the real
environment without any prior examples.

Tests, in order:
 1. token retrieval (and whether the expiration was read),
 2. a simple message - shows the RAW response (secrets masked) next to what
    each configured extraction path pulled out, pointing out empty paths,
 3. a call with a tool (did native tool calling work, or is textual mode needed),
 4. streaming.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig, load_config
from .logging_setup import get_logger
from .providers.base import Message, ToolSpec
from .providers.generic_http import GenericHTTPProvider, extract_path
from .providers.registry import ProviderRegistry
from .util import mask_mapping

log = get_logger("wotan.doctor", component="doctor")

REPORT: list[str] = []


@dataclass
class DoctorReport:
    provider_id: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, extra: dict[str, Any] | None = None) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail, **(extra or {})})

    def render(self) -> str:
        lines = [f"=== wotan doctor: provider {self.provider_id} ==="]
        for c in self.checks:
            mark = "[OK]" if c["ok"] else "[FAIL]"
            lines.append(f"{mark} {c['check']}: {c['detail']}")
            for k, v in c.items():
                if k in ("check", "ok", "detail"):
                    continue
                lines.append(f"       {k}: {v}")
        return "\n".join(lines)


def _mask(obj: Any) -> Any:
    return mask_mapping(obj) if isinstance(obj, dict) else obj


async def doctor_provider(cfg: AppConfig, provider_id: str, include_raw: bool = True) -> DoctorReport:
    report = DoctorReport(provider_id=provider_id)
    reg = ProviderRegistry(cfg)
    provider = reg.get(provider_id)

    # 1) token retrieval ------------------------------------------------------
    tokens = getattr(provider, "tokens", None)
    if tokens is not None:
        try:
            info = tokens.debug_info()
            await tokens.get_token()
            info = tokens.debug_info()
            report.add(
                "token",
                bool(info["has_token"]),
                f"auth_type={info['auth_type']} token={info['masked_token']} "
                f"expiry_source={info['expires_source'] or 'n/a'} "
                f"expires_in={info['seconds_to_expiry']}s" if info["has_token"] else "no token retrieved",
                {"token_url": info["token_url"], "expires_read": bool(info["expires_source"] and info["expires_source"] != "fixed_ttl"),
                 "masked_response": _mask(tokens.masked_last_response())},
            )
        except Exception as exc:
            report.add("token", False, f"token retrieval failed: {exc}")
            return report
    else:
        report.add("token", True, "auth=none (no token needed)")

    # 2) simple message + extraction paths ------------------------------------
    try:
        result = await provider.chat([Message(role="user", content="Reply with the single word: pong")], model=None)
        raw = getattr(provider, "last_response_debug", None)
        ok = bool(result.text)
        detail = f"text extracted: {result.text[:80]!r}, usage={result.usage}"
        extra: dict[str, Any] = {}
        if include_raw:
            extra["raw_response(masked)"] = _mask(raw)
        if isinstance(provider, GenericHTTPProvider):
            resp_paths = provider.chat_cfg.response
            for label, path in (
                ("text", resp_paths.text),
                ("tool_calls", resp_paths.tool_calls),
                ("stop_reason", resp_paths.stop_reason),
                ("usage.input", resp_paths.usage_input),
                ("usage.output", resp_paths.usage_output),
                ("error", resp_paths.error),
            ):
                if not path:
                    extra[f"path:{label}"] = "(not configured)"
                    continue
                val = extract_path(raw, path) if raw is not None else None
                empty = val in (None, "", [], {})
                extra[f"path:{label}"] = f"{path} -> {'EMPTY' if empty else truncate_repr(val)}"
                if label == "text" and empty:
                    ok = False
                    detail += " | text path came back EMPTY - adjust chat.response.text in the YAML"
        report.add("message", ok, detail, extra)
    except Exception as exc:
        report.add("message", False, f"chat failed: {exc}")
        return report

    # 3) tool call ------------------------------------------------------------
    tool = ToolSpec(name="ping_tool", description="Test tool: returns pong", parameters={"type": "object", "properties": {}})
    try:
        result = await provider.chat(
            [Message(role="user", content="CALL_TOOL: call the ping_tool function now")],
            tools=[tool], model=None,
        )
        native = bool(result.tool_calls)
        extra = {}
        if include_raw:
            extra["raw_response(masked)"] = _mask(getattr(provider, "last_response_debug", None))
        if native:
            detail = f"native tool calls work: {[(t.name, t.arguments) for t in result.tool_calls]}"
            extra["native_tools"] = True
        else:
            detail = "no tool calls parsed - set native_tools: false (textual mode) or check chat.response.tool_calls path"
            extra["native_tools"] = False
            extra["textual_hint"] = "the provider can still work with the <<<WOTAN_TOOL>>> textual protocol"
        report.add("tool_call", native, detail, extra)
    except Exception as exc:
        report.add("tool_call", False, f"tool call test failed: {exc}")

    # 3.5) OpenRouter extras: public model catalog + key credits -------------
    fetch_credits = getattr(provider, "fetch_credits", None)
    if fetch_credits is not None:  # OpenRouterProvider
        list_models_url = (getattr(provider, "cfg", None) and provider.cfg.list_models.get("url")) or ""
        if list_models_url:
            try:
                import httpx

                client = await provider._get_client()
                headers = await provider._request_headers()
                resp = await client.get(list_models_url, headers=headers)
                if resp.status_code < 400:
                    models = [m.get("id", "") for m in (resp.json().get("data") or []) if m.get("id")]
                    report.add("model_catalog", True, f"{len(models)} models available from {list_models_url}",
                               {"sample": models[:8]})
                else:
                    report.add("model_catalog", False, f"HTTP {resp.status_code} from {list_models_url}")
            except Exception as exc:
                report.add("model_catalog", False, f"catalog fetch failed: {exc}")
        try:
            credits = await fetch_credits()
            if credits.get("ok"):
                report.add("credits", True,
                           f"label={credits.get('label') or 'n/a'} usage={credits.get('usage')} "
                           f"limit={credits.get('limit')} total_credits={credits.get('total_credits')}")
            else:
                report.add("credits", False, str(credits.get("error")))
        except Exception as exc:
            report.add("credits", False, f"credits check failed: {exc}")

    # 4) streaming ------------------------------------------------------------
    caps = provider.capabilities()
    if not caps.get("streaming"):
        report.add("streaming", True, "streaming not configured (type=none) - skipped")
    else:
        chunks: list[str] = []

        async def on_event(ev: Any) -> None:
            if ev.type == "text_delta":
                chunks.append(ev.text)

        try:
            t0 = time.time()
            result = await provider.chat(
                [Message(role="user", content="STREAM: reply with one short sentence")],
                stream=True, on_event=on_event,
            )
            ok = bool(chunks) or bool(result.text)
            detail = f"received {len(chunks)} chunks in {time.time() - t0:.2f}s, text={(result.text or ''.join(chunks))[:80]!r}"
            report.add("streaming", ok, detail)
        except Exception as exc:
            report.add("streaming", False, f"streaming failed: {exc}")

    await reg.aclose()
    return report


def truncate_repr(val: Any, limit: int = 120) -> str:
    s = json.dumps(val, ensure_ascii=False, default=str) if not isinstance(val, str) else val
    return s if len(s) <= limit else s[: limit - 3] + "..."


async def doctor_all(config_path: Any = None, workspace: Any = None) -> str:
    cfg = load_config(config_path, workspace)
    outputs = []
    for p in cfg.providers:
        if not p.enabled:
            continue
        report = await doctor_provider(cfg, p.id)
        outputs.append(report.render())
    if not outputs:
        return "no providers configured - see config.example.yaml"
    return "\n\n".join(outputs)


def main_sync(config_path: Any = None, workspace: Any = None) -> str:  # pragma: no cover - CLI
    return asyncio.run(doctor_all(config_path, workspace))
