"""Anthropic Messages API adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..auth.token_manager import AuthConfigParsed, TokenManager
from ..config import ProviderConfig
from ..logging_setup import get_logger
from .base import ChatResult, LLMProvider, Message, ModelInfo, OnEvent, StreamEvent, ToolCall, ToolSpec, Usage
from .errors import LLMError, LLMRateLimitError, LLMTimeoutError

log = get_logger("wotan.providers.anthropic", component="providers")

ANTHROPIC_VERSION = "2023-06-01"


def messages_to_anthropic(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n".join(m.content for m in messages if m.role == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "assistant":
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
        elif m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or "(no output)"}
                    ],
                }
            )
        else:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
            for a in m.attachments:
                if a.kind == "image":
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": a.mime, "data": a.data_b64}})
                else:
                    blocks.append(
                        {
                            "type": "document",
                            "source": {"type": "base64", "media_type": a.mime, "data": a.data_b64},
                        }
                    )
            out.append({"role": "user", "content": blocks})
    return system, out


class AnthropicProvider(LLMProvider):
    def __init__(self, provider: ProviderConfig, client: httpx.AsyncClient | None = None) -> None:
        self.cfg = provider
        self.id = provider.id
        self.name = provider.name or provider.id
        self.base_url = (provider.base_url or "https://api.anthropic.com").rstrip("/")
        self._client = client
        self._owns_client = client is None
        auth_raw = dict(provider.auth or {})
        auth_raw.setdefault("type", "api_key")
        auth_raw.setdefault("header_name", "x-api-key")
        auth_raw.setdefault("header_prefix", "")
        self.auth_cfg = AuthConfigParsed.from_dict(auth_raw, fallback_api_key_ref=provider.api_key_ref)
        if not self.auth_cfg.api_key_ref and provider.api_key_ref:
            self.auth_cfg.api_key_ref = provider.api_key_ref
        self.tokens = TokenManager(self.auth_cfg, client=None)
        self.last_request_debug: dict[str, Any] = {}
        self.last_response_debug: Any = None
        self.default_model = provider.models[0].id if provider.models else ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=float(self.cfg.raw.get("timeout_seconds", 180)))
        return self._client

    async def _headers(self) -> dict[str, str]:
        h = {"anthropic-version": ANTHROPIC_VERSION, **dict(self.cfg.raw.get("headers") or {})}
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
        system, msgs = messages_to_anthropic(messages)
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": msgs,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [t.to_anthropic() for t in tools]
        want_stream = bool(stream if stream is not None else on_event)
        if want_stream:
            body["stream"] = True
        url = f"{self.base_url}/v1/messages"
        self.last_request_debug = {"url": url, "body": body}
        client = await self._get_client()

        # Retry loop aligned with openai_compat: 401 refresh-once, 429/5xx
        # backoff, timeouts and transport errors are retried - a single blip
        # from the endpoint must not kill the agent turn.
        max_retries = int(self.cfg.raw.get("max_retries", 3))
        backoff_base = float(self.cfg.raw.get("backoff_base_seconds", 1.0))
        last_exc: Exception | None = None
        for attempt_i in range(max(1, max_retries)):
            headers = await self._headers()
            try:
                if want_stream:
                    async with client.stream("POST", url, json=body, headers=headers) as resp:
                        if resp.status_code == 401 and attempt_i == 0:
                            self.tokens._info = None
                            await self.tokens.refresh("force")
                            continue
                        if resp.status_code == 429:
                            from .http_retry import parse_retry_after, sleep_backoff

                            retry_after = parse_retry_after(resp.headers.get("Retry-After"), backoff_base * (2**attempt_i))
                            await resp.aclose()
                            last_exc = LLMRateLimitError(f"rate limited by Anthropic endpoint (attempt {attempt_i + 1})")
                            await asyncio.sleep(min(retry_after, 30.0))
                            continue
                        if resp.status_code in (500, 502, 503, 504):
                            await resp.aclose()
                            last_exc = LLMError(f"Anthropic endpoint transient error HTTP {resp.status_code}", retryable=True)
                            await sleep_backoff(backoff_base, attempt_i)
                            continue
                        if resp.status_code >= 400:
                            text = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                            raise LLMError(f"HTTP {resp.status_code}", status=resp.status_code, why=text,
                                           how_to_fix="check base_url, x-api-key and model id")
                        return await self._consume(resp, on_event)
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 401 and attempt_i == 0:
                    self.tokens._info = None
                    await self.tokens.refresh("force")
                    continue
                if resp.status_code == 429:
                    from .http_retry import parse_retry_after

                    retry_after = parse_retry_after(resp.headers.get("Retry-After"), backoff_base * (2**attempt_i))
                    last_exc = LLMRateLimitError(f"rate limited by Anthropic endpoint (attempt {attempt_i + 1})")
                    await asyncio.sleep(min(retry_after, 30.0))
                    continue
                if resp.status_code in (500, 502, 503, 504):
                    from .http_retry import sleep_backoff

                    last_exc = LLMError(f"Anthropic endpoint transient error HTTP {resp.status_code}", retryable=True)
                    await sleep_backoff(backoff_base, attempt_i)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(
                        f"HTTP {resp.status_code}", status=resp.status_code, why=resp.text[:500],
                        how_to_fix="check base_url, x-api-key and model id",
                    )
                raw = resp.json()
                self.last_response_debug = raw
                return self._parse(raw)
            except httpx.TimeoutException as exc:
                from .http_retry import sleep_backoff

                last_exc = LLMTimeoutError(f"Anthropic request timed out: {exc}")
                log.warning("anthropic request timeout", extra={"data": {"url": url, "attempt": attempt_i}})
                await sleep_backoff(backoff_base, attempt_i)
            except Exception as exc:
                from .http_retry import is_retryable_exception, sleep_backoff

                if is_retryable_exception(exc):
                    last_exc = LLMError(
                        f"connection error talking to the Anthropic endpoint: {type(exc).__name__}: {exc}",
                        retryable=True,
                        why="network-level failure (connection reset / DNS / proxy hiccup)",
                        how_to_fix="the adapter retries with backoff automatically; check the network if it persists",
                    )
                    log.warning("anthropic transport error", extra={"data": {"url": url, "attempt": attempt_i, "error": str(exc)}})
                    await sleep_backoff(backoff_base, attempt_i)
                    continue
                raise
        raise last_exc or LLMError("request failed")

    def _parse(self, raw: dict[str, Any]) -> ChatResult:
        result = ChatResult(stop_reason=raw.get("stop_reason") or "stop")
        for block in raw.get("content") or []:
            if block.get("type") == "text":
                result.text += block.get("text", "")
            elif block.get("type") == "tool_use":
                result.tool_calls.append(
                    ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input") or {})
                )
        u = raw.get("usage") or {}
        result.usage = Usage(input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0))
        return result

    async def _consume(self, resp: httpx.Response, on_event: OnEvent | None) -> ChatResult:
        result = ChatResult()
        current_tool: dict[str, Any] | None = None
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            if etype == "content_block_start":
                cb = obj.get("content_block") or {}
                if cb.get("type") == "tool_use":
                    current_tool = {"id": cb.get("id", ""), "name": cb.get("name", ""), "input": ""}
            elif etype == "content_block_delta":
                delta = obj.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    result.text += delta["text"]
                    if on_event:
                        r = on_event(StreamEvent(type="text_delta", text=delta["text"]))
                        if r is not None:
                            await r
                elif delta.get("type") == "input_json_delta" and current_tool is not None:
                    current_tool["input"] += delta.get("partial_json", "")
            elif etype == "content_block_stop":
                if current_tool is not None:
                    try:
                        args = json.loads(current_tool["input"] or "{}")
                    except json.JSONDecodeError:
                        args = {"_raw": current_tool["input"]}
                    tc = ToolCall(id=current_tool["id"], name=current_tool["name"], arguments=args if isinstance(args, dict) else {"value": args})
                    result.tool_calls.append(tc)
                    if on_event:
                        r = on_event(StreamEvent(type="tool_call", tool_call=tc))
                        if r is not None:
                            await r
                    current_tool = None
            elif etype == "message_delta":
                result.stop_reason = (obj.get("delta") or {}).get("stop_reason") or result.stop_reason
                u = (obj.get("usage") or {})
                if u.get("output_tokens") is not None:
                    result.usage.output_tokens = int(u["output_tokens"])
            elif etype == "message_start":
                u = ((obj.get("message") or {}).get("usage")) or {}
                if u.get("input_tokens") is not None:
                    result.usage.input_tokens = int(u["input_tokens"])
        return result

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m.id, name=m.name or m.id) for m in self.cfg.models]

    async def aclose(self) -> None:
        await self.tokens.aclose()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
