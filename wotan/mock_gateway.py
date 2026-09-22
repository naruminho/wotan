"""Mock server for the corporate LLM gateway and identity API (FastAPI).

The fictional contract deliberately DIFFERS from OpenAI's field names so the
GenericHTTPProvider must be configured to match it - this is exactly the
situation in a corporate network. Simulates token expiry, 401s, errors, and
responses with and without tool calls ("actions").

Endpoints
---------
POST /mock/identity/token        - oauth_like_token identity API (form or JSON)
POST /mock/api/generate          - chat completion (fictional contract)
POST /mock/api/generate/stream   - SSE streaming (chunks with delta_text)
GET  /mock/api/models            - model catalog (auto-discovery)
GET  /mock/api/workflows         - workflow catalog (auto-discovery)
POST /mock/api/workflows/{name}  - external workflow execution

Behavior is driven by the prompt text (deterministic for tests):
  "HTTP401"   -> 401 (expired/invalid token simulation)
  "HTTP500"   -> 500 error body in the fictional error field
  "CALL_TOOL" -> response contains an action (tool call)
  "ENTITIES"  -> JSON entity list (used by the example experiment)
  "STREAM"    -> streaming-friendly text
  otherwise   -> echo with a fictional wrapper
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, Form, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

DEFAULT_TTL = int(os.environ.get("MOCK_TOKEN_TTL", "300"))
DEFAULT_TOKEN = "mock-token-"

_tokens: dict[str, float] = {}  # token -> expiry epoch
_clients = {"demo-client": "demo-secret"}


def _now() -> float:
    return time.time()


def _make_token(client_id: str, ttl: int) -> str:
    exp = _now() + ttl
    payload = {"sub": client_id, "iat": int(_now()), "exp": int(exp)}
    jwt_like = "eyJ" + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".mocksig"
    token = DEFAULT_TOKEN + uuid.uuid4().hex[:8] + "." + jwt_like.split(".")[1][:24]
    _tokens[token] = exp
    return token


def _check_auth(authorization: str | None) -> dict[str, Any] | None:
    """Return None when authorized, otherwise an error body."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return {"err": {"code": "unauthorized", "msg": "missing bearer token"}}
    token = authorization.split(" ", 1)[1].strip()
    exp = _tokens.get(token)
    if exp is None:
        return {"err": {"code": "unauthorized", "msg": "unknown token"}}
    if exp < _now():
        return {"err": {"code": "unauthorized", "msg": "token expired"}}
    return None


router = APIRouter()


@router.post("/mock/identity/token")
async def identity_token(
    request: Request,
    grant_type: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    content_type: str = Header(""),
) -> Response:
    body_json: dict[str, Any] = {}
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        body_json = await request.json()
        grant_type = body_json.get("grant_type", grant_type)
        client_id = body_json.get("client_id", client_id)
        client_secret = body_json.get("client_secret", client_secret)
    if client_id not in _clients or _clients.get(client_id) != client_secret:
        return JSONResponse({"err": {"code": "invalid_client", "msg": "bad credentials"}}, status_code=401)
    ttl = int(body_json.get("ttl", DEFAULT_TTL))
    token = _make_token(client_id, ttl)
    # Fictional identity contract: token under 'tok', expiry as 'valid_for_sec'.
    return JSONResponse({"tok": token, "token_type": "Bearer", "valid_for_sec": ttl, "issued_by": "mock-identity"})


def _extract_prompt(payload: dict[str, Any]) -> str:
    parts = []
    for inst in payload.get("instances") or []:
        parts.append(str(inst.get("input_text", "")))
        parts.append(str(inst.get("system_hint", "")))
    parts.append(str(payload.get("prompt", "")))
    return "\n".join(parts)


def _respond_for(prompt: str, model_id: str) -> dict[str, Any]:
    if "HTTP401" in prompt:
        return {"_status": 401, "err": {"code": "unauthorized", "msg": "token expired"}}
    if "HTTP500" in prompt:
        return {"_status": 500, "err": {"code": "internal", "msg": "simulated backend failure"}}
    if "ENTITIES" in prompt:
        # Deterministic entity extraction over the provided text (example experiment).
        text = prompt.split("ENTITIES", 1)[1]
        entities = []
        for name in ("Acme Corp", "Maria Silva", "Lisbon", "Contract", "Wotan"):
            if name.lower() in text.lower():
                entities.append({"name": name, "type": "ORG" if "Corp" in name or name == "Contract" else ("PERSON" if "Maria" in name else "LOC" if name == "Lisbon" else "PRODUCT")})
        output = json.dumps({"entities": entities}, ensure_ascii=False)
        return {
            "prediction": {"output_text": output, "actions": [], "finish": "stop"},
            "meta": {"tokens_in": len(prompt) // 4, "tokens_out": len(output) // 4, "request_id": uuid.uuid4().hex[:10]},
            "err": None,
        }
    if "CALL_TOOL" in prompt and model_id != "mock-weak":
        return {
            "prediction": {
                "output_text": "I will call the tool.",
                "actions": [
                    {"action_name": "fs_read", "action_input": {"path": "README.md"}, "action_id": "act_01"},
                ],
                "finish": "actions",
            },
            "meta": {"tokens_in": 12, "tokens_out": 8, "request_id": uuid.uuid4().hex[:10]},
            "err": None,
        }
    if "CALL_TOOL" in prompt and model_id == "mock-weak":
        text = (
            "Let me read the file.\n"
            "<<<WOTAN_TOOL>>>\n"
            '{"name": "fs_read", "arguments": {"path": "README.md"}}\n'
            "<<<WOTAN_TOOL_END>>>"
        )
        return {
            "prediction": {"output_text": text, "actions": [], "finish": "stop"},
            "meta": {"tokens_in": 12, "tokens_out": 20, "request_id": uuid.uuid4().hex[:10]},
            "err": None,
        }
    if "STREAM" in prompt:
        output = "Streaming works: accentos ok (acao, acentuacao)."
        return {
            "prediction": {"output_text": output, "actions": [], "finish": "stop"},
            "meta": {"tokens_in": 5, "tokens_out": len(output) // 4, "request_id": uuid.uuid4().hex[:10]},
            "err": None,
        }
    output = f"[mock:{model_id}] echo: {prompt[:200]}"
    return {
        "prediction": {"output_text": output, "actions": [], "finish": "stop"},
        "meta": {"tokens_in": len(prompt) // 4, "tokens_out": len(output) // 4, "request_id": uuid.uuid4().hex[:10]},
        "err": None,
    }


@router.post("/mock/api/generate")
async def generate(request: Request, authorization: str | None = Header(None)) -> Response:
    err = _check_auth(authorization)
    if err:
        return JSONResponse(err, status_code=401)
    payload = await request.json()
    if payload.get("stream") or (payload.get("params") or {}).get("stream"):
        return await _stream_response(payload)
    prompt = _extract_prompt(payload)
    model_id = str(payload.get("model_id", "mock-chat"))
    body = _respond_for(prompt, model_id)
    status = body.pop("_status", 200)
    return JSONResponse(body, status_code=status)


async def _stream_response(payload: dict) -> Response:
    prompt = _extract_prompt(payload)
    model_id = str(payload.get("model_id", "mock-chat"))
    body = _respond_for(prompt, model_id)
    if body.get("err"):
        return JSONResponse(body, status_code=body.pop("_status", 500))
    text = body["prediction"]["output_text"]

    async def gen():
        # Fictional SSE shape: {"piece": {"delta_text": "..."}}
        words = text.split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            yield f'data: {json.dumps({"piece": {"delta_text": chunk}}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"piece": {"delta_text": ""}, "meta": body["meta"]}, ensure_ascii=False)}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/mock/api/generate/stream")
async def generate_stream(request: Request, authorization: str | None = Header(None)) -> Response:
    err = _check_auth(authorization)
    if err:
        return JSONResponse(err, status_code=401)
    payload = await request.json()
    return await _stream_response(payload)
    prompt = _extract_prompt(payload)
    model_id = str(payload.get("model_id", "mock-chat"))
    body = _respond_for(prompt, model_id)
    if body.get("err"):
        return JSONResponse(body, status_code=body.pop("_status", 500))
    text = body["prediction"]["output_text"]

    async def gen():
        # Fictional SSE shape: {"piece": {"delta_text": "..."}}
        words = text.split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            yield f'data: {json.dumps({"piece": {"delta_text": chunk}}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"piece": {"delta_text": ""}, "meta": body["meta"]}, ensure_ascii=False)}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/mock/api/models")
async def models(authorization: str | None = Header(None)) -> Response:
    err = _check_auth(authorization)
    if err:
        return JSONResponse(err, status_code=401)
    return JSONResponse(
        {
            "catalog": [
                {"model_id": "mock-chat", "name": "Mock Chat", "context_window": 32000},
                {"model_id": "mock-weak", "name": "Mock Weak (textual tools)", "context_window": 8000},
                {"model_id": "mock-vision", "name": "Mock Vision", "context_window": 16000, "accepts_images": True},
            ]
        }
    )


@router.get("/mock/api/workflows")
async def workflows(authorization: str | None = Header(None)) -> Response:
    err = _check_auth(authorization)
    if err:
        return JSONResponse(err, status_code=401)
    return JSONResponse({"flows": [{"flow_name": "ticket_lookup", "title": "Look up a ticket"}]})


@router.post("/mock/api/workflows/{name}")
async def run_workflow(name: str, request: Request, authorization: str | None = Header(None)) -> Response:
    err = _check_auth(authorization)
    if err:
        return JSONResponse(err, status_code=401)
    payload = await request.json()
    return JSONResponse({"output": {"workflow": name, "echo_inputs": payload, "status": "done"}})


def create_mock_app() -> FastAPI:
    app = FastAPI(title="Wotan mock gateway", docs_url=None, redoc_url=None)
    app.include_router(router)

    @app.post("/mock/control/reset")
    async def reset() -> dict[str, Any]:
        _tokens.clear()
        return {"reset": True}

    @app.post("/mock/control/expire")
    async def expire() -> dict[str, Any]:
        for t in _tokens:
            _tokens[t] = _now() - 1
        return {"expired": len(_tokens)}

    @app.post("/mock/control/short_ttl")
    async def short_ttl(seconds: int = 1) -> dict[str, Any]:
        global DEFAULT_TTL
        DEFAULT_TTL = max(1, seconds)
        return {"ttl": DEFAULT_TTL}

    return app


app = create_mock_app()


def main() -> None:  # pragma: no cover - manual use
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_PORT", "8787")))


if __name__ == "__main__":  # pragma: no cover
    main()
