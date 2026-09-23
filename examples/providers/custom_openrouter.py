"""OpenRouter (openrouter.ai) provider plugin - one API key, hundreds of models.

UPDATE-PROOF by design: this file is NOT part of the wotan package. Deploy it
once and wotan upgrades (git pull, ``pip -U``) never touch it:

1. copy this file to the user-level plugin folder::

       %USERPROFILE%\\.wotan\\providers\\custom_openrouter.py    (Windows)
       ~/.wotan/providers/custom_openrouter.py                  (Linux/macOS)

2. reference it in config.yaml (see the preset in config.example.yaml)::

       - id: openrouter
         type: custom
         plugin: custom_openrouter
         api_key: env:OPENROUTER_API_KEY
         models: [...]

This is the same policy as corporate gateway adapters: ``generic_http``
covers everything YAML can express; anything beyond that lives in a
``custom_*.py`` plugin like this one, outside the source tree.

Implementation: a thin specialization of the OpenAI-compatible adapter with
OpenRouter's conventions baked in as DEFAULTS (every value overridable in
YAML):

- ``base_url``    -> https://openrouter.ai/api/v1
- attribution     -> ``HTTP-Referer`` / ``X-Title`` headers (used by
  openrouter.ai app rankings; user-provided headers win)
- ``list_models`` -> GET /models (public endpoint, OpenAI ``data[]`` shape)
- :meth:`fetch_credits` -> GET /credits (key balance/usage; ``wotan doctor``
  surfaces it automatically whenever a provider exposes this method)

Auth is the standard ``api_key`` type: ``Authorization: Bearer sk-or-...`` -
reference it as ``api_key: env:OPENROUTER_API_KEY`` (never inline in YAML).

Streaming/tool-calls/retries come free from :class:`OpenAICompatProvider`
(OpenRouter sends ``: OPENROUTER PROCESSING`` SSE keep-alive comment lines,
which the parser already ignores because they do not start with ``data:``).

NOTE: plugins are loaded by file path, so imports here MUST be absolute
(``from wotan...``); package-relative imports do not resolve.
"""

from __future__ import annotations

from typing import Any

from wotan.config import ProviderConfig
from wotan.providers.openai_compat import OpenAICompatProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_SITE_URL = "https://github.com/naruminho/wotan"
DEFAULT_SITE_NAME = "Wotan"


class Provider(OpenAICompatProvider):
    """``type: custom`` + ``plugin: custom_openrouter`` -> this class."""

    def __init__(self, provider: ProviderConfig, client: Any = None) -> None:
        raw = dict(provider.raw or {})
        # base_url default (explicit config wins)
        if not provider.base_url:
            provider.base_url = str(raw.get("base_url") or OPENROUTER_BASE_URL)
        # attribution headers (user headers win)
        headers = dict(raw.get("headers") or {})
        headers.setdefault("HTTP-Referer", DEFAULT_SITE_URL)
        headers.setdefault("X-Title", DEFAULT_SITE_NAME)
        raw["headers"] = headers
        provider.raw = raw
        # public models endpoint (explicit list_models config wins)
        if not provider.list_models:
            provider.list_models = {"url": f"{provider.base_url.rstrip('/')}/models"}
        super().__init__(provider, client=client)

    async def fetch_credits(self) -> dict[str, Any]:
        """Key balance/usage from GET /credits (error dict on failure)."""
        import httpx

        client = await self._get_client()
        headers = await self._request_headers()
        url = f"{self.base_url.rstrip('/')}/credits"
        try:
            resp = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            return {"ok": False, "error": f"timeout fetching {url}"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"network error fetching {url}: {exc}"}
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code} fetching {url}", "body": resp.text[:300]}
        data = resp.json().get("data") or {}
        return {
            "ok": True,
            "label": data.get("label") or "",
            "usage": data.get("usage"),
            "limit": data.get("limit"),
            "total_credits": data.get("total_credits"),
        }


# readable alias (the registry looks for 'Provider' or 'CustomProvider')
OpenRouterProvider = Provider
