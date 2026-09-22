"""OpenAI-compatible chat completions adapter (also used to prove the generic
provider reproduces a known contract - see config.example.yaml)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..auth.token_manager import AuthConfigParsed, TokenManager
from ..config import ProviderConfig
from ..logging_setup import get_logger
from .base import (
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
from .errors import LLMError, LLMRateLimitError, LLMTimeoutError
from .http_retry import is_retryable_exception, parse_retry_after, sleep_backoff

log = get_logger("wotan.providers.openai", component="providers")


def messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        item: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in m.tool_calls
            ]
        if m.tool_call_id:
            item["tool_call_id"] = m.tool_call_id
        if m.name and m.role == "tool":
            item["name"] = m.name
        if m.attachments:
            content: list[dict[str, Any]] = [{"type": "text", "text": m.content or " "}]
            for a in m.attachments:
                if a.kind == "image":
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": a.url or f"data:{a.mime};base64,{a.data_b64}"},
                        }
                    )
                else:
                    content.append(
                        {
                            "type": "file",
                            "file": {"filename": a.name or a.path or "file", "file_data": f"data:{a.mime};base64,{a.data_b64}"},
                        }
                    )
            item["content"] = content
        out.append(item)
    return out


class OpenAICompatProvider(LLMProvider):
    def __init__(self, provider: ProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = provider
        self.id = provider.id
        self.name = provider.name or provider.id
        self.base_url = (provider.base_url or "https://api.openai.com/v1").rstrip("/")
        self._client = client
        self._owns_client = client is None
        auth_raw = dict(provider.auth or {})
        self.auth_cfg = AuthConfigParsed.from_dict(
            {**auth_raw, "type": auth_raw.get("type", "api_key")}, fallback_api_key_ref=provider.api_key_ref
        )
        if not self.auth_cfg.api_key_ref and provider.api_key_ref:
            self.auth_cfg.api_key_ref = provider.api_key_ref
            self.auth_cfg.type = "api_key"
            self.auth_cfg.header_prefix = "Bearer "
        self.tokens = TokenManager(self.auth_cfg, client=None)
        self.last_request_debug: dict[str, Any] = {}
        self.last_response_debug: Any = None
        self.default_model = provider.models[0].id if provider.models else ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=float(self.cfg.raw.get("timeout_seconds", 180)))
        return self._client

    def _headers(self) -> dict[str, str]:
        return dict(self.cfg.raw.get("headers") or {})

    async def _request_headers(self) -> dict[str, str]:
        h = self._headers()
        await self.tokens.apply_auth(h)
        h.setdefault("Content-Type", "application/json")
        return h

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
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages_to_openai(messages),
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
            body["tool_choice"] = "auto"
        want_stream = bool(stream if stream is not None else on_event)
        if want_stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        url = f"{self.base_url}/chat/completions"
        self.last_request_debug = {"url": url, "body": body}
        client = await self._get_client()

        max_retries = int(self.cfg.raw.get("max_retries", 3))
        backoff_base = float(self.cfg.raw.get("backoff_base_seconds", 1.0))
        last_exc: Exception | None = None
        for attempt_i in range(max(1, max_retries)):
            headers = await self._request_headers()
            try:
                if want_stream:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code == 401 and attempt_i == 0:
                            self.tokens._info = None
                            await self.tokens.refresh("force")
                            continue  # retry immediately with a fresh token, doesn't count against backoff
                        if resp.status_code == 429:
                            retry_after = parse_retry_after(resp.headers.get("Retry-After"), backoff_base * (2**attempt_i))
                            await resp.aclose()
                            last_exc = LLMRateLimitError(f"rate limited by OpenAI-compatible endpoint (attempt {attempt_i + 1})")
                            await asyncio.sleep(min(retry_after, 30.0))
                            continue
                        if resp.status_code in (500, 502, 503, 504):
                            await resp.aclose()
                            last_exc = LLMError(f"OpenAI-compatible endpoint transient error HTTP {resp.status_code}", retryable=True)
                            await sleep_backoff(backoff_base, attempt_i)
                            continue
                        if resp.status_code >= 400:
                            text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                            raise LLMError(f"HTTP {resp.status_code}", status=resp.status_code, why=text,
                                           how_to_fix="check base_url, api_key and model id in the provider settings")
                        return await self._consume(resp, on_event)
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 401 and attempt_i == 0:
                    self.tokens._info = None
                    await self.tokens.refresh("force")
                    continue
                if resp.status_code == 429:
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"), backoff_base * (2**attempt_i))
                    last_exc = LLMRateLimitError(f"rate limited by OpenAI-compatible endpoint (attempt {attempt_i + 1})")
                    await asyncio.sleep(min(retry_after, 30.0))
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    last_exc = LLMError(f"OpenAI-compatible endpoint transient error HTTP {resp.status_code}", retryable=True)
                    await sleep_backoff(backoff_base, attempt_i)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(
                        f"HTTP {resp.status_code}",
                        status=resp.status_code,
                        why=resp.text[:500],
                        how_to_fix="check base_url, api_key and model id in the provider settings",
                    )
                raw = resp.json()
                self.last_response_debug = raw
                return self._parse(raw)
            except httpx.TimeoutException as exc:
                last_exc = LLMTimeoutError(f"OpenAI-compatible request timed out: {exc}")
                log.warning("openai-compatible request timeout", extra={"data": {"url": url, "attempt": attempt_i}})
                await sleep_backoff(backoff_base, attempt_i)
            except Exception as exc:
                if is_retryable_exception(exc):
                    last_exc = LLMError(
                        f"connection error talking to the endpoint: {type(exc).__name__}: {exc}",
                        retryable=True,
                        why="network-level failure (connection reset / DNS / proxy hiccup)",
                        how_to_fix="the adapter retries with backoff automatically; check the gateway URL/VPN if it persists",
                    )
                    log.warning("openai-compatible transport error", extra={"data": {"url": url, "attempt": attempt_i, "error": str(exc)}})
                    await sleep_backoff(backoff_base, attempt_i)
                    continue
                raise
        raise last_exc or LLMError("request failed")

    def _parse(self, raw: dict[str, Any]) -> ChatResult:
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        result = ChatResult(
            text=msg.get("content") or "",
            stop_reason=choice.get("finish_reason") or ("tool_calls" if msg.get("tool_calls") else "stop"),
        )
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            result.tool_calls.append(
                ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args, raw_arguments=str(args_raw))
            )
        u = raw.get("usage") or {}
        result.usage = Usage(input_tokens=int(u.get("prompt_tokens") or 0), output_tokens=int(u.get("completion_tokens") or 0))
        return result

    async def _consume(self, resp: httpx.Response, on_event: OnEvent | None) -> ChatResult:
        result = ChatResult()
        tool_acc: dict[int, dict[str, str]] = {}
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("[DONE]", ""):
                if payload == "[DONE]":
                    break
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or ""
            if text:
                result.text += text
                if on_event:
                    r = on_event(StreamEvent(type="text_delta", text=text))
                    if r is not None:
                        await r
            for tc in delta.get("tool_calls") or []:
                idx = int(tc.get("index", 0))
                acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    acc["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    acc["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["args"] += fn["arguments"]
            if choice.get("finish_reason"):
                result.stop_reason = choice["finish_reason"]
            u = obj.get("usage") or {}
            if u:
                result.usage = Usage(input_tokens=int(u.get("prompt_tokens") or 0), output_tokens=int(u.get("completion_tokens") or 0))
        for idx in sorted(tool_acc):
            acc = tool_acc[idx]
            if not acc["name"]:
                continue
            try:
                args = json.loads(acc["args"] or "{}")
            except json.JSONDecodeError:
                args = {"_raw": acc["args"]}
            tc = ToolCall(id=acc["id"] or f"call_{idx}", name=acc["name"], arguments=args if isinstance(args, dict) else {"value": args}, raw_arguments=acc["args"])
            result.tool_calls.append(tc)
            if on_event:
                r = on_event(StreamEvent(type="tool_call", tool_call=tc))
                if r is not None:
                    await r
        if result.tool_calls:
            result.stop_reason = "tool_calls"
        return result

    async def list_models(self) -> list[ModelInfo]:
        if self.cfg.list_models.get("url"):
            try:
                client = await self._get_client()
                headers = await self._request_headers()
                resp = await client.get(self.cfg.list_models["url"], headers=headers)
                if resp.status_code < 400:
                    data = resp.json().get("data", [])
                    return [ModelInfo(id=m.get("id", "")) for m in data if m.get("id")]
            except Exception:
                pass
        return [ModelInfo(id=m.id, name=m.name or m.id) for m in self.cfg.models]

    async def aclose(self) -> None:
        await self.tokens.aclose()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
