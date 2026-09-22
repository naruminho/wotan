"""Provider + session resilience: connection resets, rate limits with
HTTP-date Retry-After, timeouts and step-level retries must never kill a turn
on the first blip.

Regression coverage for the failure modes reported in the field:
- openai_compat used to CRASH with ValueError on `Retry-After: Wed, 21 Oct ...`
  (HTTP-date) because float() raised inside the retry loop;
- openai_compat had NO retry for httpx.TransportError (one connection reset
  killed the turn);
- anthropic_provider had NO retry at all (a single 429 killed the turn);
- the agent loop gave up on any LLMError even when retryable=True.
"""

from __future__ import annotations

import httpx
import pytest

from wotan.agent.session import AgentSession
from wotan.config import parse_config
from wotan.providers.base import ChatResult, LLMProvider, Message, Usage
from wotan.providers.errors import LLMError, LLMRateLimitError, LLMTimeoutError
from wotan.providers.http_retry import backoff_seconds, parse_retry_after
from wotan.providers.openai_compat import OpenAICompatProvider

from tests.test_agent_loop import FakeProvider, FakeRegistry


# ---------------------------------------------------------------------------
# http_retry helpers
# ---------------------------------------------------------------------------

def test_parse_retry_after_seconds():
    assert parse_retry_after("12", 1.0) == 12.0


def test_parse_retry_after_http_date_does_not_crash():
    value = "Wed, 21 Oct 2026 07:28:00 GMT"
    out = parse_retry_after(value, 3.0)
    assert 0.0 <= out <= 300.0


def test_parse_retry_after_garbage_uses_fallback():
    assert parse_retry_after("soon-ish", 2.5) == 2.5
    assert parse_retry_after(None, 2.5) == 2.5
    assert parse_retry_after("", 2.5) == 2.5


def test_backoff_is_capped_and_jittered():
    for attempt in range(10):
        s = backoff_seconds(1.0, attempt, cap=8.0)
        assert 0.0 <= s <= 8.0 * 1.01
    # monotone-ish: attempt 0 stays small
    assert backoff_seconds(1.0, 0, cap=30.0, jitter=0.0) == 1.0


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter
# ---------------------------------------------------------------------------

def _provider(handler, **raw) -> OpenAICompatProvider:
    import os

    cfg = parse_config({"providers": [{"id": "x", "type": "openai_compatible", "api_key": "env:X", "models": [{"id": "m"}], **raw}]})
    os.environ["X"] = "dummy-key"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(cfg.providers[0], client=client)


def _ok_body() -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.mark.asyncio
async def test_openai_retries_connection_reset():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset by peer")
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openai_survives_http_date_retry_after():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # HTTP-date in the past -> immediate retry; used to raise ValueError
            return httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2020 07:28:00 GMT"})
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openai_retries_timeout_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"


@pytest.mark.asyncio
async def test_openai_raises_retryable_after_exhausting_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    provider = _provider(handler, max_retries=2, backoff_base_seconds=0.01)
    with pytest.raises(LLMError) as excinfo:
        await provider.chat([Message(role="user", content="hi")])
    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_openai_non_4xx_client_errors_do_not_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    provider = _provider(handler, backoff_base_seconds=0.01)
    with pytest.raises(LLMError):
        await provider.chat([Message(role="user", content="hi")])
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------

def _anthropic_provider(handler, **raw):
    import os

    cfg = parse_config({"providers": [{"id": "a", "type": "anthropic", "api_key": "env:X", "models": [{"id": "m"}], **raw}]})
    os.environ["X"] = "dummy-key"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    from wotan.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(cfg.providers[0], client=client)


@pytest.mark.asyncio
async def test_anthropic_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "pong"}], "usage": {"input_tokens": 1, "output_tokens": 1}, "stop_reason": "end_turn"})

    provider = _anthropic_provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_anthropic_retries_5xx_and_connection_reset():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")
        if calls["n"] == 2:
            raise httpx.ReadError("connection reset")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}], "usage": {}, "stop_reason": "end_turn"})

    provider = _anthropic_provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "ok"
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# Session step-level retry
# ---------------------------------------------------------------------------

class FlakyProvider(LLMProvider):
    """Fails N times with a retryable error, then answers."""

    id = "flaky"
    name = "Flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=None, stream=None, on_event=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError("gateway reset", retryable=True)
        return ChatResult(text="recuperado", usage=Usage(1, 1))


@pytest.mark.asyncio
async def test_session_survives_transient_provider_errors(ws, db):
    collected: list[dict] = []

    async def emit(e: dict) -> None:
        collected.append(e)

    cfg = parse_config({"permissions": {"default_mode": "autonomous"}, "verification": {"on_stop_hook": False}, "limits": {"llm_step_retries": 2}})
    session = AgentSession(cfg, ws, db, emit)
    session.registry = FakeRegistry(FlakyProvider(failures=2))  # type: ignore[assignment]
    result = await session.run_turn("oi")
    assert result["status"] == "done", result
    assert result["text"] == "recuperado"
    warnings = [e for e in collected if e["type"] == "warning"]
    assert len(warnings) == 2  # one per failed attempt


@pytest.mark.asyncio
async def test_session_fails_after_exhausting_retries(ws, db):
    collected: list[dict] = []

    async def emit(e: dict) -> None:
        collected.append(e)

    cfg = parse_config({"permissions": {"default_mode": "autonomous"}, "verification": {"on_stop_hook": False}, "limits": {"llm_step_retries": 1}})
    session = AgentSession(cfg, ws, db, emit)
    session.registry = FakeRegistry(FlakyProvider(failures=99))  # type: ignore[assignment]
    result = await session.run_turn("oi")
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_session_does_not_retry_permanent_errors(ws, db):
    collected: list[dict] = []

    async def emit(e: dict) -> None:
        collected.append(e)

    class Permanent(LLMProvider):
        id = "perm"
        name = "Perm"
        calls = 0

        async def chat(self, messages, tools=None, model=None, temperature=0.2, max_tokens=None, stream=None, on_event=None):
            type(self).calls += 1
            raise LLMError("invalid api key", retryable=False)

    cfg = parse_config({"permissions": {"default_mode": "autonomous"}, "verification": {"on_stop_hook": False}, "limits": {"llm_step_retries": 3}})
    session = AgentSession(cfg, ws, db, emit)
    session.registry = FakeRegistry(Permanent())  # type: ignore[assignment]
    result = await session.run_turn("oi")
    assert result["status"] == "error"
    assert Permanent.calls == 1  # no retry on non-retryable
