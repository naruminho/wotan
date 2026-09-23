"""OpenRouter provider PLUGIN: defaults, attribution headers, public model
catalog, credits endpoint, and tolerance of OpenRouter's SSE keep-alive.

The provider lives OUTSIDE the package (examples/providers/custom_openrouter.py,
deployed to %USERPROFILE%\\.wotan\\providers\\) so wotan upgrades never touch
it. These tests load the file through the REAL plugin mechanism
(registry._load_plugin / ProviderRegistry) - not a package import - to prove
the deployment path works on a stock wotan.

Regression guard: OpenRouter sends ': OPENROUTER PROCESSING' comment lines in
its SSE stream; a parser that treats every line as JSON must skip them.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx
import pytest

from wotan.config import ConfigError, parse_config
from wotan.providers.base import Message
from wotan.providers.registry import ProviderRegistry, _load_plugin

_REPO = Path(__file__).resolve().parents[1]
PLUGIN_FILE = _REPO / "examples" / "providers" / "custom_openrouter.py"

_mod = _load_plugin("custom_openrouter", [PLUGIN_FILE.parent])
assert _mod is not None, "custom_openrouter.py plugin failed to load"
OpenRouterProvider = _mod.Provider


def _provider(handler, **overrides):
    cfg_dict = {
        "id": "or",
        "type": "custom",
        "plugin": "custom_openrouter",
        "api_key": "env:OPENROUTER_API_KEY",
        "models": [{"id": "openai/gpt-4o-mini"}],
        **overrides,
    }
    cfg = parse_config({"providers": [cfg_dict]})
    os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test-key-000000")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterProvider(cfg.providers[0], client=client)


def _ok_body(text: str = "pong") -> dict:
    return {
        "id": "gen-1",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }


def test_registry_loads_plugin_from_wotan_home(tmp_path, monkeypatch):
    """End-to-end: file copied to %USERPROFILE%\\.wotan\\providers\\ + type: custom."""
    home = tmp_path / "home"
    (home / "providers").mkdir(parents=True)
    shutil.copy(PLUGIN_FILE, home / "providers" / "custom_openrouter.py")
    monkeypatch.setenv("WOTAN_HOME", str(home))
    cfg = parse_config({"providers": [{
        "id": "or", "type": "custom", "plugin": "custom_openrouter",
        "api_key": "env:OPENROUTER_API_KEY", "models": [{"id": "openai/gpt-4o-mini"}],
    }]})
    prov = ProviderRegistry(cfg).create(cfg.providers[0])
    assert type(prov).__name__ == "Provider"
    assert prov.base_url == "https://openrouter.ai/api/v1"


def test_defaults_are_applied():
    provider = _provider(lambda r: httpx.Response(200, json=_ok_body()))
    assert provider.base_url == "https://openrouter.ai/api/v1"
    headers = provider.cfg.raw["headers"]
    assert headers["HTTP-Referer"] == "https://github.com/naruminho/wotan"
    assert headers["X-Title"] == "Wotan"
    assert provider.cfg.list_models["url"].endswith("/models")
    assert provider.default_model == "openai/gpt-4o-mini"


def test_user_overrides_win():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler,
                         base_url="https://proxy.corp/openrouter/v1",
                         headers={"X-Title": "MyTeam"})
    assert provider.base_url == "https://proxy.corp/openrouter/v1"
    assert provider.cfg.raw["headers"]["X-Title"] == "MyTeam"
    # HTTP-Referer default still present
    assert provider.cfg.raw["headers"]["HTTP-Referer"] == "https://github.com/naruminho/wotan"
    assert provider.cfg.list_models["url"] == "https://proxy.corp/openrouter/v1/models"


@pytest.mark.asyncio
async def test_chat_sends_bearer_and_attribution_headers():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["referer"] = request.headers.get("HTTP-Referer")
        seen["title"] = request.headers.get("X-Title")
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert seen["auth"] == "Bearer sk-or-test-key-000000"
    assert seen["referer"] == "https://github.com/naruminho/wotan"
    assert seen["title"] == "Wotan"


@pytest.mark.asyncio
async def test_streaming_skips_openrouter_keepalive_comments():
    """OpenRouter emits ': OPENROUTER PROCESSING' SSE comments between chunks."""
    sse = "\n".join([
        ": OPENROUTER PROCESSING",
        'data: {"id":"g1","choices":[{"delta":{"content":"Ol"}}]}',
        ": OPENROUTER PROCESSING",
        'data: {"id":"g1","choices":[{"delta":{"content":"á"}}]}',
        'data: {"id":"g1","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}',
        "data: [DONE]",
        "",
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse.encode("utf-8"), headers={"Content-Type": "text/event-stream"})

    provider = _provider(handler)
    result = await provider.chat([Message(role="user", content="hi")], stream=True,
                                 on_event=lambda ev: None)
    assert result.text == "Olá"  # accents survive; comments ignored


@pytest.mark.asyncio
async def test_list_models_parses_openrouter_catalog():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [
            {"id": "openai/gpt-4o", "name": "GPT-4o", "context_length": 128000},
            {"id": "anthropic/claude-sonnet-4.5", "name": "Claude Sonnet"},
        ]})

    provider = _provider(handler)
    models = await provider.list_models()
    assert [m.id for m in models] == ["openai/gpt-4o", "anthropic/claude-sonnet-4.5"]


@pytest.mark.asyncio
async def test_fetch_credits_reports_balance():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/credits")
        assert request.headers.get("Authorization", "").startswith("Bearer sk-or-")
        return httpx.Response(200, json={"data": {"label": "team", "usage": 1.25, "limit": 20.0, "total_credits": 20.0}})

    provider = _provider(handler)
    credits = await provider.fetch_credits()
    assert credits["ok"] is True
    assert credits["usage"] == 1.25 and credits["limit"] == 20.0 and credits["total_credits"] == 20.0


@pytest.mark.asyncio
async def test_fetch_credits_clean_on_bad_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid key"}})

    provider = _provider(handler)
    credits = await provider.fetch_credits()
    assert credits["ok"] is False
    assert "HTTP 401" in credits["error"]


def test_stock_config_accepts_type_custom_and_rejects_openrouter_type():
    """The isolation contract: stock config.py already whitelists `custom`;
    there is deliberately NO builtin `openrouter` type (the plugin is the
    single source of truth, so upgrades can't break it)."""
    cfg = parse_config({"providers": [{"id": "x", "type": "custom", "plugin": "custom_openrouter",
                                       "api_key": "env:K", "models": [{"id": "m"}]}]})
    assert cfg.providers[0].type == "custom"
    with pytest.raises(ConfigError, match="unknown type"):
        parse_config({"providers": [{"id": "y", "type": "openrouter", "api_key": "env:K",
                                     "models": [{"id": "m"}]}]})


@pytest.mark.asyncio
async def test_rate_limit_and_reset_still_retried_for_openrouter():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler, backoff_base_seconds=0.01)
    result = await provider.chat([Message(role="user", content="hi")])
    assert result.text == "pong"
    assert calls["n"] == 2  # inherits openai_compat retry/resilience
