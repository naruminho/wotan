"""OpenAICompatProvider retry/backoff on transient HTTP errors.

Regression: chat() used to raise on the very first 429 with no retry at all,
even though LLMRateLimitError is explicitly modeled as retryable=True and the
generic_http provider already implements this same backoff pattern. In
practice this meant a single rate-limit blip from the gateway killed the
whole agent turn instantly, with no attempt to recover.
"""

from __future__ import annotations

import json

import httpx
import pytest

from wotan.config import parse_config
from wotan.providers.base import Message
from wotan.providers.errors import LLMRateLimitError
from wotan.providers.openai_compat import OpenAICompatProvider


def _provider(handler, **raw_overrides) -> OpenAICompatProvider:
    cfg = parse_config({
        "providers": [{
            "id": "x", "type": "openai_compatible", "api_key": "env:X",
            "models": [{"id": "m"}], **raw_overrides,
        }]
    })
    import os

    os.environ["X"] = "dummy-key"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(cfg.providers[0], client=client)


def _openai_success_body() -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_retries_after_single_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=_openai_success_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_exhausts_retries_and_raises_rate_limit():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "slow down"})

    provider = _provider(handler, max_retries=3, backoff_base_seconds=0.01)
    with pytest.raises(LLMRateLimitError):
        await provider.chat([Message(role="user", content="hi")])
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_retries_transient_5xx():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="upstream busy")
        return httpx.Response(200, json=_openai_success_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 2
