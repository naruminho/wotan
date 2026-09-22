"""Provider registry: builds adapters from configuration, loads Python plugins.

Escape hatch: a ``providers/custom_*.py`` file (user-level
``~/.wotan/providers/`` or workspace ``.wotan/providers/``) can define
``class Provider(LLMProvider)`` and be selected with ``type: custom`` and
``plugin: custom_mygateway`` - for cases YAML cannot cover.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ..config import AppConfig, ProviderConfig
from ..logging_setup import get_logger
from ..paths import wotan_home
from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .generic_http import GenericHTTPProvider
from .openai_compat import OpenAICompatProvider

log = get_logger("wotan.providers.registry", component="providers")

_BUILTIN: dict[str, type] = {
    "openai_compatible": OpenAICompatProvider,
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "generic_http": GenericHTTPProvider,
}


def _load_plugin(module_name: str, search_dirs: list[Path]) -> Any | None:
    for d in search_dirs:
        f = d / f"{module_name}.py"
        if f.is_file():
            spec = importlib.util.spec_from_file_location(f"wotan_plugin_{module_name}", f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
    return None


class ProviderRegistry:
    """Holds one LLMProvider instance per configured provider."""

    def __init__(self, config: AppConfig, workspace: Path | None = None) -> None:
        self.config = config
        self.workspace = workspace
        self._instances: dict[str, LLMProvider] = {}

    def _plugin_dirs(self) -> list[Path]:
        dirs = [wotan_home() / "providers"]
        if self.workspace is not None:
            dirs.append(self.workspace / ".wotan" / "providers")
        return dirs

    def create(self, pc: ProviderConfig) -> LLMProvider:
        if pc.type in _BUILTIN:
            return _BUILTIN[pc.type](pc)
        if pc.type == "custom":
            mod = _load_plugin(pc.plugin or pc.id, self._plugin_dirs())
            if mod is None:
                raise RuntimeError(
                    f"provider {pc.id!r}: plugin module {pc.plugin or pc.id!r} not found in "
                    + ", ".join(str(d) for d in self._plugin_dirs())
                )
            cls = getattr(mod, "Provider", None) or getattr(mod, "CustomProvider", None)
            if cls is None:
                raise RuntimeError(f"plugin {pc.plugin!r} must define a 'Provider' class")
            return cls(pc)
        raise RuntimeError(f"unknown provider type {pc.type!r}")

    def get(self, provider_id: str) -> LLMProvider:
        if provider_id not in self._instances:
            pc = self.config.find_provider(provider_id)
            if pc is None:
                raise KeyError(f"unknown provider {provider_id!r}")
            self._instances[provider_id] = self.create(pc)
        return self._instances[provider_id]

    def default(self) -> LLMProvider:
        if self.config.default_provider:
            return self.get(self.config.default_provider)
        enabled = [p for p in self.config.providers if p.enabled]
        if not enabled:
            raise RuntimeError(
                "no LLM providers configured - edit ~/.wotan/config.yaml "
                "(see config.example.yaml) or use the Settings screen"
            )
        return self.get(enabled[0].id)

    def resolve(self, model_ref: str = "") -> tuple[LLMProvider, str]:
        """Resolve 'provider/model' (or default) to (provider, model_id)."""
        model_ref = (model_ref or "").strip()
        if not model_ref:
            if self.config.default_model:
                found = self.config.find_model(self.config.default_model)
                if found:
                    return self.get(found[0].id), found[1].id
            prov = self.default()
            pc = self.config.find_provider(prov.id)
            model_id = pc.models[0].id if pc and pc.models else ""
            return prov, model_id
        found = self.config.find_model(model_ref)
        if found:
            return self.get(found[0].id), found[1].id
        # provider id only
        pc = self.config.find_provider(model_ref)
        if pc:
            return self.get(pc.id), pc.models[0].id if pc.models else ""
        if "/" in model_ref:
            pid, mid = model_ref.split("/", 1)
            pc = self.config.find_provider(pid)
            if pc:
                return self.get(pid), mid
        # bare model on default provider
        return self.default(), model_ref

    async def aclose(self) -> None:
        for inst in self._instances.values():
            try:
                await inst.aclose()
            except Exception:
                pass
        self._instances.clear()
