"""Mock gateway behavior: identity tokens, expiry, generate, streaming, errors."""

from __future__ import annotations

import json

import httpx


async def test_identity_token_contract(mock_server):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/identity/token",
                         data={"grant_type": "client_credentials", "client_id": "demo-client", "client_secret": "demo-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["tok"].startswith("mock-token-")
    assert body["valid_for_sec"] > 0


async def test_identity_bad_credentials(mock_server):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/identity/token",
                         data={"client_id": "x", "client_secret": "y"})
    assert r.status_code == 401


async def _auth_header(mock_server) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/identity/token",
                         data={"client_id": "demo-client", "client_secret": "demo-secret"})
    return "Bearer " + r.json()["tok"]


async def test_generate_requires_token(mock_server):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate", json={"instances": []})
    assert r.status_code == 401


async def test_generate_fictional_shape(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"model_id": "mock-chat", "instances": [{"input_text": "hi"}], "params": {}},
                         headers={"Authorization": h})
    body = r.json()
    assert "prediction" in body and "meta" in body and "err" in body
    assert body["prediction"]["output_text"].startswith("[mock:mock-chat]")
    assert body["meta"]["tokens_in"] >= 0


async def test_token_expiry_control(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        await c.post(f"{mock_server.base_url}/mock/control/expire")
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"instances": [{"input_text": "hi"}]}, headers={"Authorization": h})
    assert r.status_code == 401
    assert "expired" in r.json()["err"]["msg"]
    h = await _auth_header(mock_server)  # fresh token works again
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"instances": [{"input_text": "hi"}]}, headers={"Authorization": h})
    assert r.status_code == 200


async def test_tool_actions(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"model_id": "mock-chat", "instances": [{"input_text": "CALL_TOOL"}]},
                         headers={"Authorization": h})
    body = r.json()
    assert body["prediction"]["actions"][0]["action_name"] == "fs_read"
    assert body["prediction"]["finish"] == "actions"


async def test_weak_model_textual_tools(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"model_id": "mock-weak", "instances": [{"input_text": "CALL_TOOL"}]},
                         headers={"Authorization": h})
    assert "<<<WOTAN_TOOL>>>" in r.json()["prediction"]["output_text"]


async def test_streaming_sse(mock_server):
    h = await _auth_header(mock_server)
    chunks = []
    async with httpx.AsyncClient() as c:
        async with c.stream("POST", f"{mock_server.base_url}/mock/api/generate/stream",
                            json={"instances": [{"input_text": "STREAM"}]}, headers={"Authorization": h}) as r:
            assert r.status_code == 200
            async for line in r.aiter_lines():
                if line.startswith("data: ") and "[DONE]" not in line:
                    chunks.append(json.loads(line[6:]))
    text = "".join(c["piece"]["delta_text"] for c in chunks if "piece" in c)
    assert "Streaming works" in text


async def test_entities_response(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"instances": [{"input_text": "ENTITIES Maria Silva works at Acme Corp in Lisbon"}]},
                         headers={"Authorization": h})
    out = json.loads(r.json()["prediction"]["output_text"])
    names = {e["name"] for e in out["entities"]}
    assert {"Maria Silva", "Acme Corp", "Lisbon"} <= names


async def test_workflows(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{mock_server.base_url}/mock/api/workflows", headers={"Authorization": h})
        assert r.json()["flows"][0]["flow_name"] == "ticket_lookup"
        r = await c.post(f"{mock_server.base_url}/mock/api/workflows/ticket_lookup",
                         json={"ticket": "T-1"}, headers={"Authorization": h})
    assert r.json()["output"]["workflow"] == "ticket_lookup"


async def test_http500_error_field(mock_server):
    h = await _auth_header(mock_server)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{mock_server.base_url}/mock/api/generate",
                         json={"instances": [{"input_text": "HTTP500"}]}, headers={"Authorization": h})
    assert r.status_code == 500
    assert r.json()["err"]["code"] == "internal"
