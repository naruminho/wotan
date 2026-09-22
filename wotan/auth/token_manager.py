"""Pluggable authentication: none | api_key | oauth_like_token.

The :class:`TokenManager` is the core of corporate gateway auth:

* calls an identity endpoint (configurable URL, method, headers, body template),
* extracts the token by a configurable path,
* reads expiration from a configurable field, the JWT ``exp`` claim or a fixed
  TTL (default 30 minutes),
* caches in memory and refreshes proactively with a configurable margin,
* is concurrency-safe (single-flight refresh),
* on 401 refreshes once and retries the call,
* reads credentials from environment variables or the system keyring,
* never writes tokens/credentials to logs (always masked),
* sends ``Authorization: Bearer <token>`` (header name and prefix configurable).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from ..config import resolve_secret
from ..llm_errors import LLMAuthError
from ..logging_setup import get_logger
from ..util import mask_secret

log = get_logger("wotan.auth", component="auth")


def _extract_path(data: Any, path: str) -> Any:
    """Dotted path with [index] support ('.' == '$' root)."""
    from ..providers.generic_http import extract_path  # local import to avoid cycle

    return extract_path(data, path)


def jwt_exp(token: str) -> float | None:
    """Read the ``exp`` claim from a JWT (payload segment only, no signature check)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


@dataclass
class TokenInfo:
    token: str
    expires_at: float | None  # epoch seconds; None = unknown (use TTL)
    source: str = ""  # response_field | jwt_exp | fixed_ttl


@dataclass
class AuthConfigParsed:
    type: str = "none"  # none | api_key | oauth_like_token
    # api_key
    api_key_ref: str = ""
    header_name: str = "Authorization"
    header_prefix: str = "Bearer "
    # oauth_like_token
    token_url: str = ""
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body_template: str = "{{ credentials | tojson }}"
    body_content_type: str = "application/json"  # or application/x-www-form-urlencoded
    token_path: str = "access_token"
    expires_path: str = ""  # e.g. 'expires_in' (seconds) or 'expires_at' (epoch)
    expires_kind: str = "seconds"  # seconds | epoch | jwt | ttl
    fixed_ttl_seconds: float = 1800.0
    refresh_margin_seconds: float = 300.0
    credentials: dict[str, str] = field(default_factory=dict)  # name -> env:/keyring: ref
    timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None, fallback_api_key_ref: str = "") -> "AuthConfigParsed":
        d = dict(d or {})
        if not d:
            return cls(type="api_key" if fallback_api_key_ref else "none", api_key_ref=fallback_api_key_ref)
        cfg = cls()
        cfg.type = d.get("type", "none")
        cfg.api_key_ref = d.get("api_key", d.get("api_key_ref", fallback_api_key_ref))
        cfg.header_name = d.get("header_name", "Authorization")
        cfg.header_prefix = d.get("header_prefix", "Bearer ")
        cfg.token_url = d.get("token_url", "")
        cfg.method = str(d.get("method", "POST")).upper()
        cfg.headers = dict(d.get("headers") or {})
        cfg.body_template = d.get("body_template", cfg.body_template)
        cfg.body_content_type = d.get("body_content_type", cfg.body_content_type)
        cfg.token_path = d.get("token_path", cfg.token_path)
        cfg.expires_path = d.get("expires_path", cfg.expires_path)
        cfg.expires_kind = d.get("expires_kind", cfg.expires_kind)
        cfg.fixed_ttl_seconds = float(d.get("fixed_ttl_seconds", d.get("ttl_seconds", cfg.fixed_ttl_seconds)))
        cfg.refresh_margin_seconds = float(d.get("refresh_margin_seconds", cfg.refresh_margin_seconds))
        cfg.credentials = dict(d.get("credentials") or {})
        cfg.timeout_seconds = float(d.get("timeout_seconds", cfg.timeout_seconds))
        return cfg


class TokenManager:
    """Concurrency-safe token cache with proactive refresh and 401 retry."""

    def __init__(self, auth: AuthConfigParsed, client: httpx.AsyncClient | None = None) -> None:
        self.auth = auth
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._info: TokenInfo | None = None
        self._last_raw: Any = None  # for doctor (masked before display)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.auth.timeout_seconds)
        return self._client

    def _credentials(self) -> dict[str, str]:
        return {k: resolve_secret(v) for k, v in self.auth.credentials.items()}

    def _build_body(self) -> tuple[str, str]:
        """Render the token request body template. Returns (body, content_type)."""
        from jinja2 import Template

        creds = self._credentials()
        ctx = {"credentials": creds, **creds}
        rendered = Template(self.auth.body_template).render(**ctx)
        return rendered, self.auth.body_content_type

    def _expiry_from(self, token: str, raw: Any) -> TokenInfo:
        a = self.auth
        exp_epoch: float | None = None
        source = "fixed_ttl"
        if a.expires_kind == "jwt" or (a.expires_path == "exp" and a.expires_kind != "seconds"):
            exp_epoch = jwt_exp(token)
            if exp_epoch:
                source = "jwt_exp"
        elif a.expires_path:
            val = _extract_path(raw, a.expires_path)
            if val is not None:
                if a.expires_kind == "epoch":
                    exp_epoch = float(val)
                    source = "response_field_epoch"
                else:
                    exp_epoch = time.time() + float(val)
                    source = "response_field_seconds"
        if exp_epoch is None:
            exp_epoch = time.time() + a.fixed_ttl_seconds
            if source == "fixed_ttl" and a.expires_kind == "jwt":
                source = "fixed_ttl(jwt_exp_missing)"
        return TokenInfo(token=token, expires_at=exp_epoch, source=source)

    async def refresh(self, reason: str = "") -> TokenInfo:
        """Fetch a new token (single-flight: concurrent callers share one request)."""
        async with self._lock:
            if self._info and not self._needs_refresh(self._info) and reason != "force":
                return self._info
            a = self.auth
            if a.type == "api_key":
                token = resolve_secret(a.api_key_ref)
                self._info = TokenInfo(token=token, expires_at=None, source="api_key")
                log.info("api key resolved", extra={"data": {"source": a.api_key_ref.split(":")[0], "masked": mask_secret(token)}})
                return self._info
            if a.type == "none":
                self._info = TokenInfo(token="", expires_at=None, source="none")
                return self._info
            if not a.token_url:
                raise LLMAuthError(
                    "oauth_like_token auth is missing 'token_url'",
                    why="the identity endpoint cannot be called without a URL",
                    how_to_fix="set auth.token_url in the provider YAML (use ${VAR} for environment-specific values)",
                )
            body, content_type = self._build_body()
            headers = dict(a.headers)
            headers["Content-Type"] = content_type
            client = await self._get_client()
            log.info("refreshing token", extra={"data": {"url": a.token_url, "reason": reason or "initial"}})
            try:
                if content_type == "application/x-www-form-urlencoded":
                    resp = await client.request(a.method, a.token_url, content=body, headers=headers)
                else:
                    resp = await client.request(a.method, a.token_url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                raise LLMAuthError(
                    f"identity endpoint unreachable: {exc}",
                    why="network error while contacting the token URL",
                    how_to_fix="check auth.token_url and network/proxy settings ('wotan doctor' repeats this test)",
                ) from exc
            if resp.status_code >= 400:
                raise LLMAuthError(
                    f"identity endpoint returned HTTP {resp.status_code}",
                    why=resp.text[:200],
                    how_to_fix="verify credentials (env/keyring) and the body_template against the identity API contract",
                )
            try:
                raw = resp.json()
            except Exception as exc:
                raise LLMAuthError(
                    "identity endpoint returned non-JSON",
                    why=str(exc),
                    how_to_fix="check the token_url path; wotan expects a JSON body with the token at auth.token_path",
                ) from exc
            self._last_raw = raw
            token = _extract_path(raw, a.token_path)
            if not token or not isinstance(token, str):
                raise LLMAuthError(
                    f"token path {a.token_path!r} extracted nothing from the identity response",
                    why=f"response keys: {list(raw) if isinstance(raw, dict) else type(raw).__name__}",
                    how_to_fix="adjust auth.token_path to where the token lives in the response JSON",
                )
            self._info = self._expiry_from(token, raw)
            log.info(
                "token refreshed",
                extra={"data": {"expires_source": self._info.source, "masked": mask_secret(token),
                               "expires_in_s": round((self._info.expires_at or 0) - time.time())}},
            )
            return self._info

    def _needs_refresh(self, info: TokenInfo) -> bool:
        if info.expires_at is None:
            return False
        return time.time() >= info.expires_at - self.auth.refresh_margin_seconds

    async def get_token(self) -> str:
        info = self._info
        if info is None or self._needs_refresh(info):
            info = await self.refresh("proactive" if info else "initial")
        return info.token

    async def apply_auth(self, headers: dict[str, str]) -> dict[str, str]:
        """Add the auth header to an outgoing request."""
        a = self.auth
        if a.type == "none":
            return headers
        token = await self.get_token()
        if not token:
            return headers
        name = a.header_name
        prefix = a.header_prefix if a.type == "oauth_like_token" or a.header_prefix != "Bearer " else a.header_prefix
        headers[name] = f"{prefix}{token}" if prefix else token
        return headers

    async def call_with_auth(
        self, do_call: Callable[[], Awaitable[httpx.Response]]
    ) -> httpx.Response:
        """Perform an HTTP call; on 401 refresh once and retry."""
        headers_note = "first attempt"
        resp = await do_call()
        if resp.status_code == 401:
            log.info("401 received - refreshing token and retrying once", extra={"data": {"attempt": headers_note}})
            self._info = None
            await self.refresh("force")
            resp = await do_call()
        return resp

    def debug_info(self) -> dict[str, Any]:
        """Masked status for 'wotan doctor' and the settings screen."""
        info = self._info
        return {
            "auth_type": self.auth.type,
            "token_url": self.auth.token_url,
            "has_token": bool(info and info.token),
            "masked_token": mask_secret(info.token) if info else "",
            "expires_at": info.expires_at if info else None,
            "expires_source": info.source if info else "",
            "seconds_to_expiry": round(info.expires_at - time.time()) if info and info.expires_at else None,
            "refresh_margin_seconds": self.auth.refresh_margin_seconds,
        }

    def masked_last_response(self) -> Any:
        from ..util import mask_mapping

        if isinstance(self._last_raw, dict):
            return mask_mapping(self._last_raw, extra_keys=("access_token", "id_token", "refresh_token", "token", "tok"))
        return self._last_raw

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
