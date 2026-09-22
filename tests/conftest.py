"""Shared fixtures: workspace, database, edit engine, live mock gateway."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from wotan.db import Database
from wotan.editing.checkpoints import CheckpointStore
from wotan.editing.engine import EditEngine


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    yield database
    database.close()


@pytest.fixture
def checkpoints(db: Database) -> CheckpointStore:
    return CheckpointStore(db)


@pytest.fixture
def engine(ws: Path, checkpoints: CheckpointStore) -> EditEngine:
    return EditEngine(ws, checkpoints=checkpoints)


@pytest.fixture
def engine_no_db(ws: Path) -> EditEngine:
    return EditEngine(ws)


class MockServer:
    def __init__(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = int(s.getsockname()[1])
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    def start(self) -> None:
        from wotan.mock_gateway import app

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(config)

        def run() -> None:
            self._server.run()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        import httpx

        while time.time() < deadline:
            try:
                r = httpx.get(f"{self.base_url}/mock/api/models", headers={"Authorization": "Bearer x"}, timeout=1)
                if r.status_code in (200, 401):
                    return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("mock server failed to start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


@pytest.fixture(scope="session")
def mock_server() -> MockServer:
    server = MockServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_identity_cfg(mock_server: MockServer) -> dict:
    """A fictional-gateway provider config matching wotan.mock_gateway."""
    return {
        "id": "fictional",
        "type": "generic_http",
        "base_url": mock_server.base_url,
        "auth": {
            "type": "oauth_like_token",
            "token_url": f"{mock_server.base_url}/mock/identity/token",
            "method": "POST",
            "body_content_type": "application/x-www-form-urlencoded",
            "body_template": "grant_type=client_credentials&client_id={{ client_id }}&client_secret={{ client_secret }}",
            "token_path": "tok",
            "expires_path": "valid_for_sec",
            "expires_kind": "seconds",
            "refresh_margin_seconds": 2,
            "credentials": {"client_id": "demo-client", "client_secret": "demo-secret"},
        },
        "chat": {
            "url": "/mock/api/generate",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body_template": (
                '{"model_id": "{{ model }}", '
                '"instances": [{"input_text": {{ (messages | map(attribute=\'content\') | join(\'\\n\')) | tojson }}, '
                '"system_hint": {{ system | tojson }}}], '
                '"params": {"temperature": {{ temperature }}, "max_output_tokens": {{ max_tokens }}, "actions": {{ tools is not none }}}}'
            ),
            "role_map": {"system": "instruction", "user": "human", "assistant": "ai", "tool": "tool_result"},
            "response": {
                "text": "prediction.output_text",
                "tool_calls": "prediction.actions",
                "tool_call": {"name": "action_name", "arguments": "action_input", "id": "action_id"},
                "stop_reason": "prediction.finish",
                "usage": {"input": "meta.tokens_in", "output": "meta.tokens_out"},
                "error": "err.msg",
                "models": "catalog",
            },
            "streaming": {"type": "sse", "delta_path": "piece.delta_text", "data_prefix": "data: "},
            "native_tools": True,
            "list_models_url": "/mock/api/models",
            "list_models_path": "catalog",
            "list_models_id_path": "model_id",
            "timeout_seconds": 30,
            "max_retries": 2,
            "backoff_base_seconds": 0.05,
        },
        "models": [
            {"id": "mock-chat", "name": "Mock Chat", "context_window": 32000},
            {"id": "mock-weak", "name": "Mock Weak", "weak": True},
        ],
    }
