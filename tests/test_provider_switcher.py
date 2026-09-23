"""Provider switcher API: visual toggle between providers (e.g. OpenRouter vs
a corporate gateway) where EACH provider records its OWN models - the last
model used per provider is restored on switch, and UI-saved model ids are
merged into the picker without touching config.yaml.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from wotan.config import parse_config
from wotan.server import create_app


def make_client(ws, db, monkeypatch, tmp_path, config: dict | None = None) -> TestClient:
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    cfg = parse_config(config or {"server": {"open_browser": False}, "providers": [
        {"id": "openrouter", "type": "openai_compatible", "api_key": "env:K", "models": [
            {"id": "anthropic/claude-x", "name": "Claude X"},
            {"id": "openai/gpt-x", "name": "GPT X"},
        ]},
        {"id": "corp", "type": "openai_compatible", "api_key": "env:K", "models": [
            {"id": "corp-big", "name": "Corp Big"},
            {"id": "corp-small", "name": "Corp Small", "weak": True},
        ]},
    ]})
    app = create_app(workspace=ws, config=cfg, db=db)
    return TestClient(app)


def test_models_reports_providers_and_default_active(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    body = client.get("/api/models").json()
    assert [p["id"] for p in body["providers"]] == ["openrouter", "corp"]
    assert body["providers"][1]["name"] == "corp" and body["providers"][1]["type"] == "openai_compatible"
    assert body["active_provider"] == "openrouter"  # first configured (no default_model set)
    assert body["last_model"] == {}
    assert set(body["groups"]) == {"openrouter", "corp"}


def test_switch_restores_each_providers_own_model(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)

    # record a model used on openrouter (composite ref with an inner slash)
    r = client.post("/api/providers/active", json={"model_ref": "openrouter/anthropic/claude-x"})
    assert r.json() == {"ok": True, "provider_id": "openrouter", "model_ref": "openrouter/anthropic/claude-x"}

    # switch to corp -> its own first model (no memory yet)
    r = client.post("/api/providers/active", json={"provider_id": "corp"})
    assert r.json() == {"ok": True, "provider_id": "corp", "model_ref": "corp/corp-big"}

    # back to openrouter -> ITS OWN remembered model comes back
    r = client.post("/api/providers/active", json={"provider_id": "openrouter"})
    assert r.json()["model_ref"] == "openrouter/anthropic/claude-x"

    # use another openrouter model; memory follows
    client.post("/api/providers/active", json={"model_ref": "openrouter/openai/gpt-x"})
    r = client.post("/api/providers/active", json={"provider_id": "corp"})
    assert r.json()["model_ref"] == "corp/corp-big"  # corp still remembers its own
    r = client.post("/api/providers/active", json={"provider_id": "openrouter"})
    assert r.json()["model_ref"] == "openrouter/openai/gpt-x"

    body = client.get("/api/models").json()
    assert body["active_provider"] == "openrouter"
    # each side recorded its own model
    assert body["last_model"] == {"openrouter": "openai/gpt-x", "corp": "corp-big"}


def test_record_custom_model_per_provider(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)

    r = client.post("/api/providers/models", json={"provider_id": "openrouter", "model_id": "x-ai/grok-4"})
    assert r.json()["ok"] is True and r.json()["models"] == ["x-ai/grok-4"]

    # merged into the picker groups without touching config.yaml
    body = client.get("/api/models").json()
    refs = [m["ref"] for m in body["groups"]["openrouter"]]
    assert "openrouter/x-ai/grok-4" in refs

    # switch straight to it (never configured in YAML)
    r = client.post("/api/providers/active", json={"model_ref": "openrouter/x-ai/grok-4"})
    assert r.json() == {"ok": True, "provider_id": "openrouter", "model_ref": "openrouter/x-ai/grok-4"}
    r = client.post("/api/providers/active", json={"provider_id": "corp"})
    r = client.post("/api/providers/active", json={"provider_id": "openrouter"})
    assert r.json()["model_ref"] == "openrouter/x-ai/grok-4"

    # duplicates are ignored; configured models are not copied into the custom list
    r = client.post("/api/providers/models", json={"provider_id": "openrouter", "model_id": "x-ai/grok-4"})
    assert r.json()["models"] == ["x-ai/grok-4"]
    r = client.post("/api/providers/models", json={"provider_id": "openrouter", "model_id": "anthropic/claude-x"})
    assert r.json()["models"] == ["x-ai/grok-4"]


def test_switch_unknown_provider_and_bad_model_id(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    assert client.post("/api/providers/active", json={"provider_id": "nope"}).json()["ok"] is False
    assert "unknown provider" in client.post("/api/providers/active", json={"provider_id": "nope"}).json()["error"]
    r = client.post("/api/providers/models", json={"provider_id": "openrouter", "model_id": "has spaces"})
    assert r.json()["ok"] is False
    r = client.post("/api/providers/models", json={"provider_id": "openrouter", "model_id": "  "})
    assert r.json()["ok"] is False
    r = client.post("/api/providers/models", json={"provider_id": "wat", "model_id": "x"})
    assert r.json()["ok"] is False


def test_stale_remembered_model_falls_back_to_first(ws, db, monkeypatch, tmp_path):
    client = make_client(ws, db, monkeypatch, tmp_path)
    client.post("/api/providers/active", json={"model_ref": "corp/corp-big"})

    # corp drops corp-big from the YAML (config hot-swapped like /api/settings does)
    new_cfg = parse_config({"providers": [
        {"id": "openrouter", "type": "openai_compatible", "api_key": "env:K", "models": [{"id": "anthropic/claude-x"}]},
        {"id": "corp", "type": "openai_compatible", "api_key": "env:K", "models": [{"id": "corp-small"}]},
    ]})
    client.app.state.config = new_cfg

    r = client.post("/api/providers/active", json={"provider_id": "corp"})
    assert r.json()["model_ref"] == "corp/corp-small"
    body = client.get("/api/models").json()
    assert body["last_model"]["corp"] == "corp-small"
