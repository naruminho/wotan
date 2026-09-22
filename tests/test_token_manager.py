"""TokenManager: caching, proactive refresh, single-flight, 401 retry, masking."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest

from wotan.auth.token_manager import AuthConfigParsed, TokenManager, jwt_exp


def _auth_cfg(mock_base: str, **kw) -> AuthConfigParsed:
    cfg = {
        "type": "oauth_like_token",
        "token_url": f"{mock_base}/mock/identity/token",
        "method": "POST",
        "body_content_type": "application/x-www-form-urlencoded",
        "body_template": "grant_type=client_credentials&client_id={{ client_id }}&client_secret={{ client_secret }}",
        "token_path": "tok",
        "expires_path": "valid_for_sec",
        "expires_kind": "seconds",
        "refresh_margin_seconds": 2,
        "credentials": {"client_id": "demo-client", "client_secret": "demo-secret"},
    }
    cfg.update(kw)
    return AuthConfigParsed.from_dict(cfg)


async def test_token_fetch_and_expiry_from_field(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url, expires_kind="seconds", expires_path="valid_for_sec"))
    token = await tm.get_token()
    assert token.startswith("mock-token-")
    info = tm.debug_info()
    assert info["has_token"]
    assert info["expires_source"] == "response_field_seconds"
    assert 250 < info["seconds_to_expiry"] <= 300
    assert "****" in info["masked_token"] or "*" in info["masked_token"]
    await tm.aclose()


async def test_token_cached_no_second_request(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url))
    t1 = await tm.get_token()
    t2 = await tm.get_token()
    assert t1 == t2  # cached
    await tm.aclose()


async def test_fixed_ttl_when_no_expires_path(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url, expires_path="", expires_kind="ttl", fixed_ttl_seconds=42))
    await tm.get_token()
    info = tm.debug_info()
    assert info["expires_source"].startswith("fixed_ttl")
    assert info["seconds_to_expiry"] <= 42
    await tm.aclose()


async def test_jwt_exp_extraction():
    payload = {"exp": int(time.time()) + 100}
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    token = f"eyJhbGciOiJIUzI1NiJ9.{p}.sig"
    exp = jwt_exp(token)
    assert exp is not None
    assert abs(exp - payload["exp"]) < 2
    assert jwt_exp("not-a-jwt") is None


async def test_jwt_expires_kind(mock_server):
    # The mock's real token is not a full JWT - jwt_exp returns None -> fixed_ttl fallback.
    tm = TokenManager(_auth_cfg(mock_server.base_url, expires_kind="jwt", fixed_ttl_seconds=55))
    await tm.get_token()
    info = tm.debug_info()
    assert "fixed_ttl" in info["expires_source"] or info["expires_source"] == "jwt_exp"
    await tm.aclose()


async def test_proactive_refresh_with_margin(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url, expires_kind="seconds", expires_path="valid_for_sec",
                                refresh_margin_seconds=10_000))  # margin larger than TTL -> always refresh
    t1 = await tm.get_token()
    t2 = await tm.get_token()
    assert t1 != t2  # proactively refreshed because within margin
    await tm.aclose()


async def test_single_flight_concurrent_refresh(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url))
    tokens = await asyncio.gather(*[tm.get_token() for _ in range(8)])
    assert len(set(tokens)) == 1  # one refresh, shared by all callers
    await tm.aclose()


async def test_401_refresh_and_retry(mock_server):
    calls = {"n": 0}

    async def do_call() -> httpx.Response:
        calls["n"] += 1
        async with httpx.AsyncClient() as client:
            headers = {}
            await tm.apply_auth(headers)
            if calls["n"] == 1:
                headers["Authorization"] = "Bearer stale"
            return await client.get(f"{mock_server.base_url}/mock/api/models", headers=headers)

    tm = TokenManager(_auth_cfg(mock_server.base_url))
    resp = await tm.call_with_auth(do_call)
    assert resp.status_code == 200
    assert calls["n"] == 2  # retried once after 401
    await tm.aclose()


async def test_bad_credentials_clear_error(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url, credentials={"client_id": "bad", "client_secret": "bad"}))
    with pytest.raises(Exception) as exc:
        await tm.get_token()
    msg = str(exc.value)
    assert "ERROR:" in msg or "401" in msg
    assert "HOW TO FIX" in msg or "how_to_fix" in msg
    await tm.aclose()


async def test_token_path_wrong_points_to_field(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url, token_path="does.not.exist"))
    with pytest.raises(Exception) as exc:
        await tm.get_token()
    assert "extracted nothing" in str(exc.value) or "token path" in str(exc.value).lower()
    await tm.aclose()


async def test_debug_info_masks_token(mock_server):
    tm = TokenManager(_auth_cfg(mock_server.base_url))
    await tm.get_token()
    info = tm.debug_info()
    assert tm._info.token not in json.dumps(info)
    last = tm.masked_last_response()
    if isinstance(last, dict):
        assert last.get("tok", "") in ("", None) or "*" in str(last.get("tok"))
    await tm.aclose()


async def test_api_key_auth():
    import os

    os.environ["WOTAN_TEST_KEY"] = "super-secret-key-123"
    tm = TokenManager(AuthConfigParsed.from_dict({"type": "api_key", "api_key": "env:WOTAN_TEST_KEY"}))
    headers = await tm.apply_auth({})
    assert headers["Authorization"] == "Bearer super-secret-key-123"
    assert tm.debug_info()["auth_type"] == "api_key"
    await tm.aclose()


async def test_none_auth():
    tm = TokenManager(AuthConfigParsed.from_dict({"type": "none"}))
    headers = await tm.apply_auth({"X": "1"})
    assert "Authorization" not in headers
    await tm.aclose()
