"""GenericHTTPProvider: a fully declarative HTTP adapter configured via YAML.

No code is needed to match a corporate LLM gateway whose contract is similar
to OpenAI's but not identical:

* URL, method and headers with environment interpolation,
* request body as a Jinja2 template (messages, tools, model, temperature,
  max_tokens, role mapping),
* response extraction by paths (JSONPath / JMESPath / dotted paths),
* optional streaming (SSE, NDJSON or none) with the text-delta path,
* ``native_tools: true/false`` - otherwise textual tool-call format,
* multimodal templates for images/documents (inline base64, prior upload, URL).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
from jinja2 import Template

from ..auth.token_manager import AuthConfigParsed, TokenManager
from ..config import ProviderConfig, interpolate_env, resolve_secret
from ..logging_setup import get_logger
from ..util import mask_mapping
from .base import (
    Attachment,
    ChatResult,
    LLMProvider,
    Message,
    ModelInfo,
    OnEvent,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
)
from .errors import LLMContractError, LLMError, LLMRateLimitError, LLMTimeoutError
from .text_tools import extract_reply_text, parse_tool_calls

log = get_logger("wotan.providers.generic", component="providers")


# ---------------------------------------------------------------------------
# Path extraction: dotted paths, [index], [*], $.-JSONPath and JMESPath
# ---------------------------------------------------------------------------

_DOT_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]|\[\*\]|\[[^\]]*\]")


def _get_segment(obj: Any, token: str) -> Any:
    if obj is None:
        return None
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        if inner == "*":
            return obj if isinstance(obj, list) else None
        if inner.startswith(("'", '"', "?")):
            return None  # handled by jmespath fallback
        try:
            idx = int(inner)
        except ValueError:
            return None
        if isinstance(obj, list):
            try:
                return obj[idx]
            except IndexError:
                return None
        return None
    if isinstance(obj, dict):
        return obj.get(token)
    return None


def extract_path(data: Any, path: str) -> Any:
    """Extract a value by path.

    Supports:
      * ``a.b.c`` and ``a.b[0].c`` dotted paths (also ``$`` / ``$.a.b`` root),
      * full JSONPath expressions (starting with ``$`` and containing filters),
      * JMESPath expressions when the ``jmespath`` package is installed.
    """
    if path in ("", "$", "$.", None):
        return data
    if not isinstance(path, str):
        return None
    p = path.strip()
    if p.startswith("$.") or p == "$":
        p = p[2:] if p.startswith("$.") else ""
    if not p:
        return data
    # Complex JSONPath (filters, recursive descent, unions) or JMESPath features.
    if "?" in p or ".." in p or "|" in p or p.startswith("{"):
        try:
            from jsonpath_ng import parse as jp_parse  # jsonpath-ng is a hard dep

            matches = [m.value for m in jp_parse(p if p.startswith("$") else "$." + p).find(data)]
            if not matches:
                return None
            return matches[0] if len(matches) == 1 else matches
        except Exception:
            pass
        # Common filter form that jsonpath-ng cannot parse: prefix[?(@.key==value)].suffix
        m = re.match(r"^([\w.$]*)\[\?\(@\.([\w]+)\s*(==|=)\s*\"?([^)\"']+)\"?\)\](?:\.(.*))?$", p)
        if m:
            prefix, key, _op, val, suffix = m.groups()
            items = extract_path(data, prefix) if prefix else data
            if isinstance(items, list):
                out = []
                for it in items:
                    if isinstance(it, dict) and str(it.get(key)) == val:
                        out.append(extract_path(it, suffix) if suffix else it)
                if not out:
                    return None
                return out[0] if len(out) == 1 else out
        try:
            import jmespath  # optional

            return jmespath.search(p, data)
        except Exception:
            return None
    # Plain dotted path with indexes / wildcard.
    current = data
    tokens = [t for t in _DOT_TOKEN.findall(p) if t != "."]
    for token in tokens:
        if token == "[*]":
            if not isinstance(current, list):
                return None
            gathered: list[Any] = []
            rest_tokens = tokens[tokens.index(token) + 1 :]
            for item in current:
                sub = item
                for rt in rest_tokens:
                    sub = _get_segment(sub, rt)
                gathered.append(sub)
            return gathered
        current = _get_segment(current, token)
        if current is None:
            return None
    return current


def _coerce_bool_path(value: Any) -> bool:
    return bool(value)


# ---------------------------------------------------------------------------
# Contract configuration (from the YAML `chat:` block)
# ---------------------------------------------------------------------------

@dataclass
class ResponsePaths:
    text: str = "text"
    tool_calls: str = ""
    tool_name: str = "name"
    tool_arguments: str = "arguments"
    tool_id: str = "id"
    stop_reason: str = ""
    usage_input: str = ""
    usage_output: str = ""
    error: str = "error"
    models: str = ""


@dataclass
class StreamingConfig:
    type: str = "none"  # none | sse | ndjson
    delta_path: str = "delta"
    data_prefix: str = "data: "
    done_markers: list[str] = field(default_factory=lambda: ["[DONE]"])
    tool_calls_path: str = ""


@dataclass
class MultimodalConfig:
    enabled: bool = False
    images_mode: str = "inline_base64"  # inline_base64 | upload_url | url
    documents_mode: str = "inline_base64"
    upload_url: str = ""
    image_template: str = '{"type": "image", "media_type": "{{ mime }}", "data": "{{ data_b64 }}"}'
    document_template: str = '{"type": "document", "media_type": "{{ mime }}", "data": "{{ data_b64 }}"}'
    upload_response_path: str = "id"
    uploaded_ref_template: str = '{"type": "image_ref", "id": "{{ ref }}"}'


@dataclass
class ChatContract:
    url: str = ""
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body_template: str = ""
    body_content_type: str = "application/json"
    role_map: dict[str, str] = field(default_factory=dict)
    response: ResponsePaths = field(default_factory=ResponsePaths)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    native_tools: bool = True
    tool_name: str = "tools"
    tools_template: str = "{{ tools | tojson }}"
    multimodal: MultimodalConfig = field(default_factory=MultimodalConfig)
    list_models_url: str = ""
    list_models_path: str = "data"
    list_models_id_path: str = "id"
    timeout_seconds: float = 180.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0


def _parse_chat(raw: dict[str, Any]) -> ChatContract:
    c = ChatContract()
    c.url = raw.get("url", "")
    c.method = str(raw.get("method", "POST")).upper()
    c.headers = dict(raw.get("headers") or {})
    c.body_template = raw.get("body_template", "")
    c.body_content_type = raw.get("body_content_type", "application/json")
    c.role_map = dict(raw.get("role_map") or {})
    resp = raw.get("response") or {}
    c.response = ResponsePaths(
        text=resp.get("text", "text"),
        tool_calls=resp.get("tool_calls", ""),
        tool_name=(resp.get("tool_call") or {}).get("name", "name"),
        tool_arguments=(resp.get("tool_call") or {}).get("arguments", "arguments"),
        tool_id=(resp.get("tool_call") or {}).get("id", "id"),
        stop_reason=resp.get("stop_reason", ""),
        usage_input=(resp.get("usage") or {}).get("input", ""),
        usage_output=(resp.get("usage") or {}).get("output", ""),
        error=resp.get("error", "error"),
        models=resp.get("models", ""),
    )
    st = raw.get("streaming") or {}
    c.streaming = StreamingConfig(
        type=st.get("type", "none"),
        delta_path=st.get("delta_path", "delta"),
        data_prefix=st.get("data_prefix", "data: "),
        done_markers=st.get("done_markers", ["[DONE]"]),
        tool_calls_path=st.get("tool_calls_path", ""),
    )
    c.native_tools = bool(raw.get("native_tools", True))
    c.tool_name = raw.get("tool_name", "tools")
    c.tools_template = raw.get("tools_template", "{{ tools | tojson }}")
    mm = raw.get("multimodal") or {}
    c.multimodal = MultimodalConfig(**{k: v for k, v in mm.items() if k in MultimodalConfig.__dataclass_fields__})
    c.list_models_url = raw.get("list_models_url", "")
    c.list_models_path = raw.get("list_models_path", "data")
    c.list_models_id_path = raw.get("list_models_id_path", "id")
    c.timeout_seconds = float(raw.get("timeout_seconds", 180.0))
    c.max_retries = int(raw.get("max_retries", 3))
    c.backoff_base_seconds = float(raw.get("backoff_base_seconds", 1.0))
    return c


def _render_json_template(template: str, ctx: dict[str, Any]) -> Any:
    """Render a Jinja2 template and parse the result as JSON (with repair)."""
    tpl = Template(template)
    tpl.globals["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
    tpl.globals["tojson_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    rendered = tpl.render(**ctx)
    rendered = rendered.strip()
    if not rendered:
        raise LLMContractError(
            "request body template rendered empty output",
            why="the Jinja2 template produced no text - check the variables it references",
            how_to_fix="make sure the template uses messages/tools/model/temperature/max_tokens as intended",
        )
    try:
        return json.loads(rendered)
    except json.JSONDecodeError:
        from .text_tools import _repair_json

        obj, _rep = _repair_json(rendered)
        return obj


class GenericHTTPProvider(LLMProvider):
    """Declarative adapter: everything comes from the YAML contract."""

    def __init__(self, provider: ProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = provider
        self.id = provider.id
        self.name = provider.name or provider.id
        self.chat_cfg = _parse_chat(provider.chat or {})
        self.base_url = (provider.base_url or "").rstrip("/")
        self._client = client
        self._owns_client = client is None
        auth_raw = dict(provider.auth or {})
        if provider.api_key_ref and "api_key" not in auth_raw:
            auth_raw = {"type": "api_key", "api_key": provider.api_key_ref, **auth_raw}
        self.auth_cfg = AuthConfigParsed.from_dict(auth_raw, fallback_api_key_ref=provider.api_key_ref)
        self.tokens = TokenManager(self.auth_cfg, client=None)
        self.last_request_debug: dict[str, Any] = {}
        self.last_response_debug: dict[str, Any] = {}
        self.multimodal_cfg = self.chat_cfg.multimodal

    def capabilities(self) -> dict[str, Any]:
        return {
            "native_tools": self.chat_cfg.native_tools,
            "streaming": self.chat_cfg.streaming.type != "none",
            "multimodal": self.multimodal_cfg.enabled,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.chat_cfg.timeout_seconds)
        return self._client

    # -- message mapping -----------------------------------------------------
    def _map_role(self, role: str) -> str:
        return self.chat_cfg.role_map.get(role, role)

    def _attachment_json(self, att: Attachment) -> Any:
        mm = self.multimodal_cfg
        from jinja2 import Template as T

        if att.kind == "image":
            mode = mm.images_mode
            tpl = mm.image_template
        else:
            mode = mm.documents_mode
            tpl = mm.document_template
        if mode == "url" and att.url:
            return T(mm.uploaded_ref_template).render(ref=att.url, url=att.url, mime=att.mime, name=att.name)
        if mode == "inline_base64":
            return json.loads(
                T(tpl).render(data_b64=att.data_b64, mime=att.mime, name=att.name, path=att.path, url=att.url)
            )
        # upload_url mode needs an HTTP upload - done in chat() before render.
        return {"pending_upload": att.name or att.path}

    def _messages_ctx(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            item: dict[str, Any] = {"role": self._map_role(m.role), "content": m.content}
            if m.tool_call_id:
                item["tool_call_id"] = m.tool_call_id
            if m.name:
                item["name"] = m.name
            if m.tool_calls:
                item["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls
                ]
            if m.attachments:
                item["attachments"] = [self._attachment_json(a) for a in m.attachments]
            out.append(item)
        return out

    async def _upload_pending(self, messages: list[Message]) -> None:
        mm = self.multimodal_cfg
        if not mm.enabled or mm.upload_url == "":
            return
        client = await self._get_client()
        for m in messages:
            for att in m.attachments:
                if (att.kind == "image" and mm.images_mode == "upload_url") or (
                    att.kind == "document" and mm.documents_mode == "upload_url"
                ):
                    if att.url:
                        continue
                    import base64 as b64

                    headers = await self.tokens.apply_auth({})
                    resp = await client.post(
                        self._full_url(mm.upload_url),
                        content=b64.b64decode(att.data_b64),
                        headers={**headers, "Content-Type": att.mime},
                    )
                    if resp.status_code >= 400:
                        raise LLMContractError(
                            f"attachment upload failed: HTTP {resp.status_code}",
                            why=resp.text[:200],
                            how_to_fix="check multimodal.upload_url and auth in the provider YAML",
                        )
                    ref = extract_path(resp.json(), mm.upload_response_path)
                    att.url = str(ref)

    def _full_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _build_body(self, messages: list[Message], tools: list[ToolSpec] | None, model: str | None,
                    temperature: float, max_tokens: int | None) -> Any:
        c = self.chat_cfg
        system_parts = [m.content for m in messages if m.role == "system"]
        tools_ctx: Any = None
        if tools and c.native_tools:
            rendered_tools = Template(c.tools_template).render(tools=[t.to_openai() for t in tools], specs=[t.to_anthropic() for t in tools])
            try:
                tools_ctx = json.loads(rendered_tools)
            except json.JSONDecodeError:
                tools_ctx = rendered_tools
        ctx = {
            "messages": self._messages_ctx(messages),
            "system": "\n".join(system_parts),
            "system_messages": system_parts,
            "tools": tools_ctx,
            "model": model or (self.cfg.models[0].id if self.cfg.models else ""),
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
            "role_map": self.chat_cfg.role_map,
        }
        body = _render_json_template(c.body_template, ctx)
        return body

    # -- response parsing -----------------------------------------------------
    def _parse_response(self, raw: Any) -> ChatResult:
        c = self.chat_cfg.response
        err = extract_path(raw, c.error) if c.error else None
        if err:
            raise LLMContractError(
                f"gateway returned an error: {err}",
                why="the response contains an error field at the configured error path",
                how_to_fix="check the request contract and credentials; 'wotan doctor' shows the raw response",
            )
        text = extract_path(raw, c.text)
        text = text if isinstance(text, str) else ("" if text is None else json.dumps(text, ensure_ascii=False))
        tool_calls: list[ToolCall] = []
        if self.chat_cfg.native_tools and c.tool_calls:
            raw_calls = extract_path(raw, c.tool_calls)
            if isinstance(raw_calls, dict):
                raw_calls = [raw_calls]
            for i, rc in enumerate(raw_calls or []):
                if not isinstance(rc, dict):
                    continue
                name = extract_path(rc, c.tool_name)
                args = extract_path(rc, c.tool_arguments)
                tc_id = extract_path(rc, c.tool_id) or f"call_{i}"
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"_raw": args}
                if name:
                    tool_calls.append(ToolCall(id=str(tc_id), name=str(name), arguments=args or {}, raw_arguments=json.dumps(args or {}, ensure_ascii=False)))
        elif not self.chat_cfg.native_tools:
            calls, _issues = parse_tool_calls(text)
            for i, pc in enumerate(calls):
                tool_calls.append(ToolCall(id=f"call_{i}", name=pc.name, arguments=pc.arguments, raw_arguments=pc.raw))
            text = extract_reply_text(text)
        usage = Usage()
        if c.usage_input:
            v = extract_path(raw, c.usage_input)
            usage.input_tokens = int(v or 0)
        if c.usage_output:
            v = extract_path(raw, c.usage_output)
            usage.output_tokens = int(v or 0)
        stop = extract_path(raw, c.stop_reason) if c.stop_reason else ""
        if not stop:
            stop = "tool_calls" if tool_calls else "stop"
        return ChatResult(text=text, tool_calls=tool_calls, stop_reason=str(stop), usage=usage, raw=raw)

    # -- streaming ------------------------------------------------------------
    async def _iter_stream_lines(self, resp: httpx.Response) -> AsyncIterator[str]:
        st = self.chat_cfg.streaming
        async for line in resp.aiter_lines():
            if st.type == "sse":
                if line.startswith(st.data_prefix):
                    payload = line[len(st.data_prefix) :].strip()
                    if payload in st.done_markers:
                        break
                    if payload:
                        yield payload
                elif line.startswith("event:"):
                    continue
            elif st.type == "ndjson":
                if line.strip() and line.strip() not in st.done_markers:
                    yield line.strip()

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_event: OnEvent | None = None,
    ) -> ChatResult:
        c = self.chat_cfg
        await self._upload_pending(messages)
        body = self._build_body(messages, tools, model, temperature, max_tokens)
        want_stream = (stream if stream is not None else c.streaming.type != "none") and on_event is not None
        if want_stream and c.streaming.type != "none":
            body = dict(body) if isinstance(body, dict) else body
            if isinstance(body, dict):
                body.setdefault("stream", True)

        url = self._full_url(c.url)
        self.last_request_debug = {"url": url, "method": c.method, "body": body}

        async def do_call(stream_mode: bool) -> httpx.Response:
            client = await self._get_client()
            headers = dict(c.headers)

            async def attempt() -> httpx.Response:
                h = dict(headers)
                await self.tokens.apply_auth(h)
                h.setdefault("Content-Type", c.body_content_type)
                req_kwargs: dict[str, Any] = {"headers": h}
                if c.body_content_type == "application/x-www-form-urlencoded":
                    req_kwargs["content"] = body if isinstance(body, str) else json.dumps(body)
                else:
                    req_kwargs["json"] = body
                request = client.build_request(c.method, url, **req_kwargs)
                return await client.send(request, stream=stream_mode)

            resp = await attempt()
            if resp.status_code == 401:
                # Refresh once and retry (TokenManager policy).
                self.tokens._info = None
                await self.tokens.refresh("force")
                resp = await attempt()
            return resp

        import asyncio

        last_exc: Exception | None = None
        for attempt_i in range(max(1, c.max_retries)):
            try:
                resp = await do_call(want_stream)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", c.backoff_base_seconds * (2**attempt_i)))
                    await resp.aclose()
                    await asyncio.sleep(min(retry_after, 30.0))
                    last_exc = LLMRateLimitError(f"rate limited (HTTP 429), attempt {attempt_i + 1}")
                    continue
                if resp.status_code in (502, 503, 504):
                    await resp.aclose()
                    await asyncio.sleep(c.backoff_base_seconds * (2**attempt_i))
                    last_exc = LLMError(f"gateway transient error HTTP {resp.status_code}", retryable=True)
                    continue
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    await resp.aclose()
                    raise LLMError(
                        f"gateway returned HTTP {resp.status_code}",
                        status=resp.status_code,
                        why=text,
                        how_to_fix="run 'wotan doctor' to see the raw response next to the configured extraction paths",
                    )
                if want_stream and c.streaming.type != "none":
                    return await self._consume_stream(resp, on_event)
                raw = resp.json()
                self.last_response_debug = raw
                return self._parse_response(raw)
            except httpx.TimeoutException as exc:
                last_exc = LLMTimeoutError(f"request timed out after {c.timeout_seconds}s")
                log.warning("gateway timeout", extra={"data": {"url": url, "attempt": attempt_i}})
            except httpx.HTTPError as exc:
                last_exc = LLMError(f"network error: {exc}", retryable=True)
                await asyncio.sleep(c.backoff_base_seconds * (2**attempt_i))
        raise last_exc or LLMError("request failed")

    async def _consume_stream(self, resp: httpx.Response, on_event: OnEvent) -> ChatResult:
        c = self.chat_cfg
        st = c.streaming
        acc_text: list[str] = []
        result = ChatResult()
        async for payload in self._iter_stream_lines(resp):
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = extract_path(obj, st.delta_path) if st.delta_path else obj
            if isinstance(delta, str) and delta:
                acc_text.append(delta)
                if on_event:
                    ev = StreamEvent(type="text_delta", text=delta)
                    r = on_event(ev)
                    if r is not None:
                        await r
            elif isinstance(delta, dict):
                # Some gateways stream {"text": ..., "tool_calls": [...]} pieces.
                piece = delta.get("text") or delta.get("content") or ""
                if isinstance(piece, str) and piece:
                    acc_text.append(piece)
                    if on_event:
                        r = on_event(StreamEvent(type="text_delta", text=piece))
                        if r is not None:
                            await r
            if st.tool_calls_path:
                tc = extract_path(obj, st.tool_calls_path)
                if tc:
                    result.raw = obj
            # usage in stream
            if c.response.usage_input or c.response.usage_output:
                ui = extract_path(obj, c.response.usage_input) if c.response.usage_input else None
                uo = extract_path(obj, c.response.usage_output) if c.response.usage_output else None
                if ui is not None:
                    result.usage.input_tokens = int(ui or 0)
                if uo is not None:
                    result.usage.output_tokens = int(uo or 0)
        text = "".join(acc_text)
        result.text = text
        if not c.native_tools:
            calls, _ = parse_tool_calls(text)
            result.tool_calls = [ToolCall(id=f"call_{i}", name=pc.name, arguments=pc.arguments) for i, pc in enumerate(calls)]
            result.text = extract_reply_text(text)
            result.stop_reason = "tool_calls" if result.tool_calls else "stop"
        else:
            result.stop_reason = "stop"
        self.last_response_debug = {"streamed_text": text}
        return result

    async def list_models(self) -> list[ModelInfo]:
        c = self.chat_cfg
        if not c.list_models_url:
            return [ModelInfo(id=m.id, name=m.name or m.id) for m in self.cfg.models]
        try:
            client = await self._get_client()
            headers = await self.tokens.apply_auth(dict(c.headers))
            resp = await client.get(self._full_url(c.list_models_url), headers=headers)
            if resp.status_code >= 400:
                return []
            raw = resp.json()
            items = extract_path(raw, c.list_models_path) or []
            out = []
            for it in items:
                if isinstance(it, str):
                    out.append(ModelInfo(id=it))
                elif isinstance(it, dict):
                    mid = extract_path(it, c.list_models_id_path) or it.get("id")
                    if mid:
                        out.append(ModelInfo(id=str(mid), name=str(it.get("name", mid))))
            return out
        except Exception as exc:
            log.warning("list_models failed", extra={"data": {"error": str(exc)}})
            return []

    async def aclose(self) -> None:
        await self.tokens.aclose()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
