"""Config parsing: provider YAML -> ProviderConfig, especially field name
translation where the YAML key differs from the dataclass attribute."""

from __future__ import annotations

from wotan.config import parse_config


def test_provider_api_key_maps_to_api_key_ref():
    # Regression: _parse_provider built ProviderConfig with a plain field-name
    # match, so YAML's `api_key:` (the documented, only supported key - see
    # config.example.yaml) never reached the `api_key_ref` attribute the
    # provider adapters actually read. Every provider using a bare `api_key:`
    # (the openai_compatible and anthropic presets, and generic_http's simple
    # auth.api_key shorthand) silently authenticated with an empty token.
    cfg = parse_config({
        "providers": [
            {"id": "x", "type": "openai_compatible", "api_key": "env:MY_KEY", "models": [{"id": "m"}]},
        ]
    })
    assert cfg.providers[0].api_key_ref == "env:MY_KEY"


def test_provider_without_api_key_leaves_ref_empty():
    cfg = parse_config({
        "providers": [{"id": "x", "type": "openai_compatible", "models": [{"id": "m"}]}]
    })
    assert cfg.providers[0].api_key_ref == ""
