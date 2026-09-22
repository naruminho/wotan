"""Server API smoke tests (FastAPI TestClient) + doctor against the mock."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from wotan.config import parse_config
from wotan.db import Database
from wotan.server import create_app


def make_client(ws, db, monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    cfg = parse_config({"server": {"open_browser": False}})
    app = create_app(workspace=ws, config=cfg, db=db)
    return TestClient(app)


def test_health(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["app"] == "Wotan"


def test_fs_file_roundtrip(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    (ws / "hello.py").write_text("print('olá')\n", encoding="utf-8")
    r = client.get("/api/fs/file", params={"path": "hello.py"})
    data = r.json()
    assert "olá" in data["content"]
    assert data["eol"] == "lf"
    r = client.put("/api/fs/file", json={"path": "hello.py", "content": "print('ação')\n", "expected_hash": data.get("hash") or __import__("wotan.editing.engine", fromlist=["sha256_text"]).sha256_text(data["content"])})
    assert r.status_code == 200
    assert "ação" in (ws / "hello.py").read_text(encoding="utf-8")


def test_fs_conflict_detected(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    (ws / "c.py").write_text("x\n", encoding="utf-8")
    r = client.put("/api/fs/file", json={"path": "c.py", "content": "y\n", "expected_hash": "wrong-hash"})
    assert r.status_code == 409


def test_fs_tree_and_search(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    (ws / "mod.py").write_text("needle = 1\n", encoding="utf-8")
    r = client.get("/api/fs/tree")
    assert any(e["name"] == "mod.py" for e in r.json()["entries"])
    r = client.get("/api/search", params={"q": "needle"})
    assert r.json()["count"] == 1


def test_path_escape_rejected(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.get("/api/fs/file", params={"path": "../etc/passwd"})
    assert r.status_code == 400


def test_models_endpoint(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.get("/api/models")
    assert "groups" in r.json()


def test_memory_api(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.post("/api/memory", json={"name": "user.md", "content": "# user\n"})
    assert r.json()["ok"]
    r = client.get("/api/memory")
    assert any(f["name"] == "user.md" for f in r.json()["files"])


def test_logs_and_feedback(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    client.post("/api/feedback", json={"kind": "js_error", "message": "ReferenceError: x", "stack": "at foo"})
    r = client.get("/api/logs", params={"level": "ERROR"})
    assert isinstance(r.json()["entries"], list)


def test_diagnostics_bundle(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.get("/api/diagnostics")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_git_status_nonrepo(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    r = client.get("/api/git/status")
    body = r.json()
    assert body["ok"] in (True, False)  # works or reports error, never 500


async def test_doctor_against_mock(mock_server, mock_identity_cfg, monkeypatch, tmp_path):
    from wotan.doctor import doctor_provider

    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    cfg = parse_config({"providers": [mock_identity_cfg]})
    report = await doctor_provider(cfg, "fictional")
    checks = {c["check"]: c for c in report.checks}
    assert checks["token"]["ok"]
    assert checks["token"]["expires_read"] is True
    assert checks["message"]["ok"]
    assert "raw_response(masked)" in checks["message"]
    assert checks["tool_call"]["ok"]  # native tool calling works against the mock
    assert checks["streaming"]["ok"]
    rendered = report.render()
    assert "[OK] token" in rendered


async def test_doctor_flags_missing_paths(mock_server, mock_identity_cfg, monkeypatch, tmp_path):
    from wotan.doctor import doctor_provider

    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    bad = json.loads(json.dumps(mock_identity_cfg))
    bad["chat"]["response"]["text"] = "does.not.exist"
    cfg = parse_config({"providers": [bad]})
    report = await doctor_provider(cfg, "fictional")
    checks = {c["check"]: c for c in report.checks}
    assert not checks["message"]["ok"]
    assert "EMPTY" in json.dumps(checks["message"])
