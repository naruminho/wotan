"""gateway_client SDK against the mock (what generated experiments use)."""

from __future__ import annotations

import pytest

from wotan import gateway_client as sdk
from wotan.config import parse_config, save_config


@pytest.fixture
def sdk_config(mock_identity_cfg, tmp_path, monkeypatch):
    cfg_data = {
        "default_provider": "fictional",
        "default_model": "fictional/mock-chat",
        "providers": [mock_identity_cfg],
        "external_tools": [
            {
                "name": "ticket_lookup",
                "description": "Look up a ticket",
                "endpoint": "/mock/api/workflows/ticket_lookup",
                "method": "POST",
                "provider_id": "fictional",
                "input_template": "{{ inputs | tojson }}",
                "result_path": "output",
            }
        ],
    }
    cfg = parse_config(cfg_data)
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg, cfg_path)
    monkeypatch.setenv("WOTAN_CONFIG", str(cfg_path))
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    sdk._config = None
    sdk._registry = None
    yield cfg_path
    import asyncio

    asyncio.run(sdk.aclose())


async def test_sdk_chat(sdk_config):
    out = await sdk.chat("hello sdk", model="fictional/mock-chat")
    assert "echo: hello sdk" in out


async def test_sdk_list_models(sdk_config):
    models = sdk.list_models()
    assert any(m["model"] == "mock-chat" for m in models)


async def test_sdk_list_models_remote(sdk_config):
    remote = await sdk.list_models_remote("fictional/mock-chat")
    assert any(m["id"] == "mock-vision" for m in remote)


async def test_sdk_extract_json(sdk_config):
    result = await sdk.extract_json(
        "ENTITIES Maria Silva works at Acme Corp in Lisbon",
        schema={"type": "object", "properties": {"entities": {"type": "array"}}},
        model="fictional/mock-chat",
    )
    assert "entities" in result
    names = {e["name"] for e in result["entities"]}
    assert "Maria Silva" in names


async def test_sdk_run_workflow(sdk_config):
    out = await sdk.run_workflow("ticket_lookup", {"ticket": "T-42"})
    assert out["workflow"] == "ticket_lookup"
    assert out["echo_inputs"]["ticket"] == "T-42"


async def test_sdk_unknown_workflow(sdk_config):
    with pytest.raises(KeyError):
        await sdk.run_workflow("nope", {})


async def test_generated_app_pattern(sdk_config, tmp_path):
    """The pattern experiment templates use: import SDK, call chat/extract_json."""
    script = tmp_path / "generated_app.py"
    script.write_text(
        "import asyncio\n"
        "from wotan.gateway_client import chat, extract_json\n\n"
        "async def main():\n"
        "    answer = await chat('ENTITIES Maria Silva at Acme Corp', model='fictional/mock-chat')\n"
        "    print(answer)\n\n"
        "asyncio.run(main())\n",
        encoding="utf-8",
    )
    import os
    import subprocess
    import sys

    from wotan.paths import PACKAGE_DIR, subprocess_utf8_env

    env = subprocess_utf8_env()
    # Works both installed (pip install wotan) and from a source checkout.
    env["PYTHONPATH"] = str(PACKAGE_DIR.parent) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60,
                          env=env)
    assert proc.returncode == 0, proc.stderr
    assert "entities" in proc.stdout
