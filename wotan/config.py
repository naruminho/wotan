"""Configuration: YAML is the source of truth.

The UI reads and writes the same YAML files. Secrets are never stored in YAML:
they are referenced as ``env:VAR_NAME`` or ``keyring:SERVICE:USERNAME`` and
resolved at runtime.

Environment interpolation is supported in every string: ``${VAR}`` and
``${VAR:-default}``.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import config_file, wotan_home
from .util import ToolError

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


class ConfigError(Exception):
    pass


def interpolate_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in strings."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name = m.group("name")
            env = os.environ.get(name)
            if env is not None:
                return env
            default = m.group("default")
            if default is not None:
                return default
            raise ConfigError(f"Environment variable {name!r} is not set and no default was provided")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    return value


def resolve_secret(ref: Any) -> str:
    """Resolve a credential reference.

    Accepted forms:
      * ``env:VAR_NAME``          - environment variable (preferred)
      * ``keyring:SERVICE:USER``  - OS keyring entry (optional `keyring` extra)
      * literal string            - allowed but discouraged; warn in docs
    """
    if ref is None:
        return ""
    if not isinstance(ref, str):
        return str(ref)
    if ref.startswith("env:"):
        var = ref[4:]
        val = os.environ.get(var)
        if val is None:
            raise ConfigError(
                ToolError(
                    error=f"credential env variable {var!r} is not set",
                    why=f"config references 'env:{var}' but the variable is missing",
                    how_to_fix=f"set {var} in the environment (or in %USERPROFILE%\\.wotan\\.env) before starting wotan",
                ).render()
            )
        return val
    if ref.startswith("keyring:"):
        parts = ref.split(":")
        if len(parts) != 3:
            raise ConfigError("keyring reference must be 'keyring:SERVICE:USERNAME'")
        _, service, username = parts
        try:
            import keyring  # type: ignore

            val = keyring.get_password(service, username)
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ConfigError(
                f"keyring lookup failed for {service}/{username}: {exc}. "
                "Install the 'keyring' extra or use an env: reference."
            ) from exc
        if not val:
            raise ConfigError(f"keyring entry {service}/{username} is empty or missing")
        return val
    return ref


def load_env_file(path: Path | None = None) -> None:
    """Load ``%USERPROFILE%\\.wotan\\.env`` (KEY=VALUE) into the environment.

    Existing environment variables win (never override silently).
    """
    p = path or (wotan_home() / ".env")
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


# ---------------------------------------------------------------------------
# Dataclass schema (validated on load)
# ---------------------------------------------------------------------------

@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True


@dataclass
class EditorConfig:
    rewrite_threshold_lines: int = 40
    new_file_encoding: str = "utf-8"
    new_file_eol: str = "lf"  # lf | crlf | preserve
    emoji_policy_enabled: bool = True
    emoji_allow: list[str] = field(default_factory=list)
    syntax_check: bool = True
    hashline_for_weak_models: bool = True


@dataclass
class PermissionsConfig:
    default_mode: str = "ask"  # ask | edits | autonomous
    always_allow: list[str] = field(default_factory=list)
    always_deny: list[str] = field(default_factory=list)
    protected_files: list[str] = field(
        default_factory=lambda: [
            "pyproject.toml",
            "setup.cfg",
            "setup.py",
            "tox.ini",
            "pytest.ini",
            ".pre-commit-config.yaml",
            "tsconfig.json",
            "package.json",
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.json",
            "biome.json",
            "oxlint.json",
        ]
    )


@dataclass
class VerificationConfig:
    enabled: bool = True
    commands: list[str] = field(default_factory=list)
    on_stop_hook: bool = True
    dirty_tracking: bool = True
    require_evidence: bool = True


@dataclass
class LimitsConfig:
    max_steps: int = 60
    max_tokens_per_task: int = 400_000
    llm_timeout_seconds: float = 180.0
    tool_timeout_seconds: float = 120.0
    stall_warning_seconds: float = 60.0
    doom_loop_window: int = 10


@dataclass
class SearchConfig:
    enabled: bool = True
    provider: str = "searxng"  # searxng | brave | tavily | http
    base_url: str = ""
    endpoint: str = "/search"
    method: str = "GET"
    api_key_ref: str = ""
    results_path: str = "results"
    title_path: str = "title"
    url_path: str = "url"
    snippet_path: str = "content"
    query_param: str = "q"
    extra_params: dict[str, str] = field(default_factory=dict)


@dataclass
class WebFetchConfig:
    enabled: bool = True
    allow_domains: list[str] = field(default_factory=list)  # empty = any (taint rules apply)


@dataclass
class HookConfig:
    event: str  # session_start | pre_tool_use | post_tool_use | pre_compact | stop
    command: str
    matcher: str = ""  # regex on tool name (optional)
    timeout_seconds: float = 30.0
    block_exit_code: int = 2


@dataclass
class HooksConfig:
    enabled: bool = True
    hooks: list[HookConfig] = field(default_factory=list)


@dataclass
class ExternalToolConfig:
    name: str
    description: str
    endpoint: str
    method: str = "POST"
    provider_id: str = ""  # reuse this provider's auth
    input_template: str = "{{ inputs | tojson }}"
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema
    headers: dict[str, str] = field(default_factory=dict)
    result_path: str = "$"
    timeout_seconds: float = 60.0


@dataclass
class McpServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class RolesConfig:
    planner: str = ""  # "provider_id/model_id" or empty = default model
    executor: str = ""
    summarizer: str = ""
    subagent: str = ""
    verifier: str = ""


@dataclass
class ModelProfile:
    id: str
    name: str = ""
    context_window: int = 128_000
    tools: bool = True
    streaming: bool = True
    weak: bool = False
    multimodal: bool = False
    edit_format: str = ""  # str_replace | apply_patch | hashline ("" = auto)
    description: str = ""


@dataclass
class ProviderConfig:
    id: str
    type: str  # openai_compatible | anthropic | generic_http | custom
    name: str = ""
    base_url: str = ""
    api_key_ref: str = ""
    auth: dict[str, Any] = field(default_factory=dict)
    chat: dict[str, Any] = field(default_factory=dict)  # generic_http contract
    models: list[ModelProfile] = field(default_factory=list)
    list_models: dict[str, Any] = field(default_factory=dict)
    multimodal: dict[str, Any] = field(default_factory=dict)
    plugin: str = ""  # providers/custom_*.py module name for type=custom
    enabled: bool = True
    raw: dict[str, Any] = field(default_factory=dict)  # full YAML block for adapters


@dataclass
class LoggingConfig:
    level: str = "INFO"
    debug_llm: bool = False


@dataclass
class AppConfig:
    version: int = 1
    server: ServerConfig = field(default_factory=ServerConfig)
    editor: EditorConfig = field(default_factory=EditorConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    web: WebFetchConfig = field(default_factory=WebFetchConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    external_tools: list[ExternalToolConfig] = field(default_factory=list)
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    providers: list[ProviderConfig] = field(default_factory=list)
    roles: RolesConfig = field(default_factory=RolesConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    default_provider: str = ""
    default_model: str = ""
    source_files: list[str] = field(default_factory=list)

    def find_provider(self, provider_id: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.id == provider_id:
                return p
        return None

    def all_models(self) -> list[tuple[ProviderConfig, ModelProfile]]:
        out: list[tuple[ProviderConfig, ModelProfile]] = []
        for p in self.providers:
            if not p.enabled:
                continue
            for m in p.models:
                out.append((p, m))
        return out

    def find_model(self, ref: str) -> tuple[ProviderConfig, ModelProfile] | None:
        """Find by 'provider/model', 'provider:model' or bare model id."""
        ref = ref.strip()
        for p, m in self.all_models():
            if ref in (f"{p.id}/{m.id}", f"{p.id}:{m.id}", m.id):
                return p, m
        return None


def _build(cls: type, data: dict[str, Any] | None, **defaults: Any) -> Any:
    data = dict(data or {})
    fields_names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {}
    for k, v in data.items():
        if k in fields_names:
            kwargs[k] = v
    for k, v in defaults.items():
        kwargs.setdefault(k, v)
    return cls(**kwargs)


def _parse_model(d: dict[str, Any]) -> ModelProfile:
    return _build(ModelProfile, d, id=d.get("id", ""))


def _parse_provider(d: dict[str, Any]) -> ProviderConfig:
    models = [_parse_model(m) for m in d.get("models") or []]
    prov = _build(
        ProviderConfig,
        {k: v for k, v in d.items() if k != "models"},
        id=d.get("id", ""),
        type=d.get("type", ""),
    )
    prov.models = models
    prov.raw = dict(d)
    return prov


def parse_config(data: dict[str, Any], source: str = "") -> AppConfig:
    data = interpolate_env(data)
    cfg = AppConfig()
    cfg.version = int(data.get("version", 1))
    cfg.server = _build(ServerConfig, data.get("server"))
    cfg.editor = _build(EditorConfig, data.get("editor"))
    cfg.permissions = _build(PermissionsConfig, data.get("permissions"))
    cfg.verification = _build(VerificationConfig, data.get("verification"))
    cfg.limits = _build(LimitsConfig, data.get("limits"))
    cfg.search = _build(SearchConfig, data.get("search"))
    cfg.web = _build(WebFetchConfig, data.get("web"))
    hooks_raw = data.get("hooks") or {}
    hooks_list = [_build(HookConfig, h, event=h.get("event", "")) for h in hooks_raw.get("hooks", []) if isinstance(h, dict)]
    cfg.hooks = HooksConfig(enabled=bool(hooks_raw.get("enabled", True)), hooks=hooks_list)
    cfg.external_tools = [
        _build(ExternalToolConfig, t, name=t.get("name", ""), description=t.get("description", ""), endpoint=t.get("endpoint", ""))
        for t in data.get("external_tools") or []
    ]
    cfg.mcp_servers = [
        _build(McpServerConfig, s, name=s.get("name", ""), command=s.get("command", ""))
        for s in data.get("mcp_servers") or []
    ]
    cfg.providers = [_parse_provider(p) for p in data.get("providers") or []]
    cfg.roles = _build(RolesConfig, data.get("roles"))
    cfg.logging = _build(LoggingConfig, data.get("logging"))
    cfg.default_provider = data.get("default_provider", "") or ""
    cfg.default_model = data.get("default_model", "") or ""
    if source:
        cfg.source_files.append(source)

    # Validation with actionable errors.
    seen: set[str] = set()
    for p in cfg.providers:
        if not p.id:
            raise ConfigError("every provider needs an 'id'")
        if p.id in seen:
            raise ConfigError(f"duplicate provider id {p.id!r}")
        seen.add(p.id)
        if p.type not in ("openai_compatible", "anthropic", "generic_http", "custom"):
            raise ConfigError(
                f"provider {p.id!r}: unknown type {p.type!r} "
                "(expected openai_compatible | anthropic | generic_http | custom)"
            )
        if p.type == "generic_http" and not p.chat:
            raise ConfigError(f"provider {p.id!r}: generic_http requires a 'chat' contract block")
    return cfg


def load_config(path: Path | None = None, workspace: Path | None = None) -> AppConfig:
    """Load ``~/.wotan/config.yaml`` then merge workspace ``.wotan/config.yaml``.

    Workspace values override user-level values (deep merge for mappings,
    replacement for lists).
    """
    load_env_file()
    primary = path or config_file()
    merged: dict[str, Any] = {}
    sources: list[str] = []

    def merge_into(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge_into(dst[k], v)
            else:
                dst[k] = v

    if primary.is_file():
        loaded = yaml.safe_load(primary.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{primary}: top level must be a mapping")
        merge_into(merged, loaded)
        sources.append(str(primary))

    if workspace is not None:
        ws_cfg = workspace / ".wotan" / "config.yaml"
        if ws_cfg.is_file():
            loaded = yaml.safe_load(ws_cfg.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                merge_into(merged, loaded)
                sources.append(str(ws_cfg))

    cfg = parse_config(merged, source=" + ".join(sources))
    return cfg


def _to_plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in dataclasses.fields(obj):
            if f.name in ("raw", "source_files"):
                continue
            out[f.name] = _to_plain(getattr(obj, f.name))
        return out
    if isinstance(obj, list):
        return [_to_plain(i) for i in obj]
    return obj


def save_config(cfg: AppConfig, path: Path | None = None) -> Path:
    """Write configuration back to YAML (the UI settings editor uses this)."""
    target = path or config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _to_plain(cfg)
    # Providers keep their full raw block so generic contracts round-trip.
    for i, p in enumerate(cfg.providers):
        raw = dict(p.raw or {})
        raw["id"] = p.id
        raw["type"] = p.type
        raw["name"] = p.name
        raw["enabled"] = p.enabled
        raw["models"] = [_to_plain(m) for m in p.models]
        if p.base_url:
            raw["base_url"] = p.base_url
        if p.api_key_ref:
            raw["api_key"] = p.api_key_ref
        if p.auth:
            raw["auth"] = p.auth
        if p.chat:
            raw["chat"] = p.chat
        if p.list_models:
            raw["list_models"] = p.list_models
        if p.multimodal:
            raw["multimodal"] = p.multimodal
        if p.plugin:
            raw["plugin"] = p.plugin
        data["providers"][i] = raw
    target.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target
