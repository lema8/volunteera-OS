"""Layered configuration loading.

Order of precedence (lowest to highest):

1. Built-in defaults (``lema.config.schema``)
2. Global ``~/.config/lema/config.toml``
3. Extra provider profiles in ``~/.config/lema/providers/*.toml``
4. Project ``./.lema/config.toml``
5. Environment variables (``LEMA_*``)
6. CLI overrides
"""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from pathlib import Path
from collections.abc import Sequence
from typing import Any, get_type_hints

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from lema.config.schema import (
    AgentConfig,
    Config,
    ContextConfig,
    LoggingConfig,
    PermissionMode,
    ProviderConfig,
    SkillsConfig,
    ToolsConfig,
    UIConfig,
)
from lema.util import paths


class ConfigError(Exception):
    """Raised when configuration on disk is malformed."""


_SECTION_TYPES = {
    "agent": AgentConfig,
    "context": ContextConfig,
    "skills": SkillsConfig,
    "tools": ToolsConfig,
    "logging": LoggingConfig,
    "ui": UIConfig,
}


_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _field_types(obj: Any) -> dict[str, Any]:
    """Resolved type hints for a dataclass.

    ``dataclasses.fields()[i].type`` is a *string* under
    ``from __future__ import annotations``, which silently defeats any
    ``is int`` style check, so hints are resolved once and cached.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    cached = _HINT_CACHE.get(cls)
    if cached is None:
        try:
            cached = get_type_hints(cls)
        except Exception:  # pragma: no cover - exotic forward refs
            cached = {f.name: f.type for f in fields(cls)}
        _HINT_CACHE[cls] = cached
    return cached


def _coerce(value: Any, target_type: Any) -> Any:
    """Best-effort coercion of TOML scalars into the dataclass field type."""
    if isinstance(target_type, str):
        # An unresolved annotation; map the common spellings by hand.
        simple = target_type.replace(" ", "").split("|")[0]
        target_type = {
            "int": int,
            "float": float,
            "bool": bool,
            "str": str,
            "Path": Path,
        }.get(simple, target_type)
    origin = getattr(target_type, "__origin__", None)
    if origin is not None:  # Optional[...] / list[...] / dict[...]
        args = [a for a in getattr(target_type, "__args__", ()) if a is not type(None)]
        if value is None:
            return None
        if origin in (list, tuple):
            return list(value)
        if origin is dict:
            return dict(value)
        if len(args) == 1:
            return _coerce(value, args[0])
        return value
    if target_type is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if target_type is int and not isinstance(value, bool):
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is str:
        return str(value)
    if target_type is Path:
        return Path(str(value)).expanduser()
    return value


def _apply_section(instance: Any, data: dict[str, Any], *, where: str) -> None:
    """Apply a TOML table onto a dataclass instance in place."""
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected a table, got {type(data).__name__}")
    hints = _field_types(instance) if is_dataclass(instance) else {}
    for key, value in data.items():
        if key not in hints:
            # Unknown keys are tolerated so that a config written for a newer
            # Lema does not break an older one.
            continue
        try:
            setattr(instance, key, _coerce(value, hints[key]))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where}.{key}: {exc}") from exc


def _provider_from_table(name: str, table: dict[str, Any], *, where: str) -> ProviderConfig:
    provider = ProviderConfig(name=name)
    # `kind` defaults to the profile name when it names a known adapter.
    if "kind" not in table and name in {"openai", "anthropic", "ollama"}:
        provider.kind = name
    _apply_section(provider, table, where=where)
    provider.name = table.get("name", name)
    return provider


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _merge_file(config: Config, path: Path) -> bool:
    """Merge one config file into ``config``. Returns True if it existed."""
    if not path.is_file():
        return False
    data = _read_toml(path)
    where = str(path)

    # Top-level scalars.
    if "permissions" in data:
        config.permissions = PermissionMode.parse(data["permissions"])
    if "debug" in data:
        config.debug = bool(data["debug"])

    # Named provider profiles: [providers.openrouter]
    for prof_name, table in (data.get("providers") or {}).items():
        if not isinstance(table, dict):
            continue
        config.providers[prof_name] = _provider_from_table(
            prof_name, table, where=f"{where}:providers.{prof_name}"
        )

    # The active provider: [provider]
    provider_table = data.get("provider")
    if isinstance(provider_table, dict):
        if set(provider_table.keys()) == {"use"} or (
            "use" in provider_table and len(provider_table) == 1
        ):
            # [provider] use = "openrouter"  -> select a named profile
            selected = provider_table["use"]
            if selected in config.providers:
                config.provider = config.providers[selected]
            else:
                raise ConfigError(f"{where}: provider.use={selected!r} is not a defined profile")
        else:
            use = provider_table.get("use")
            if not use:
                # [provider] name = "work" also selects a profile defined in
                # providers/work.toml; remaining keys override it.
                candidate = provider_table.get("name")
                if candidate in config.providers:
                    use = candidate
            base = config.providers.get(use) if use else None
            if base is not None:
                import copy

                config.provider = copy.deepcopy(base)
            _apply_section(config.provider, provider_table, where=f"{where}:provider")
            if "name" in provider_table:
                config.provider.name = str(provider_table["name"])
            elif "kind" in provider_table:
                config.provider.name = str(provider_table["kind"])

    for section, cls in _SECTION_TYPES.items():
        table = data.get(section)
        if isinstance(table, dict):
            _apply_section(getattr(config, section), table, where=f"{where}:{section}")

    config.sources.append(str(path))
    return True


def _load_provider_profiles_dir(config: Config, directory: Path) -> None:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.toml")):
        data = _read_toml(path)
        name = data.get("name") or path.stem
        config.providers[name] = _provider_from_table(name, data, where=str(path))
        config.sources.append(str(path))


_ENV_MAP: dict[str, tuple[str, str]] = {
    "LEMA_PROVIDER": ("provider", "kind"),
    "LEMA_PROVIDER_NAME": ("provider", "name"),
    "LEMA_MODEL": ("provider", "model"),
    "LEMA_BASE_URL": ("provider", "base_url"),
    "LEMA_API_KEY": ("provider", "api_key"),
    "LEMA_API_KEY_ENV": ("provider", "api_key_env"),
    "LEMA_TEMPERATURE": ("provider", "temperature"),
    "LEMA_CONTEXT_WINDOW": ("provider", "context_window"),
    "LEMA_MAX_TOKENS": ("provider", "max_tokens"),
    "LEMA_TOOL_MODE": ("provider", "tool_mode"),
    "LEMA_TIMEOUT": ("provider", "timeout"),
    "LEMA_MAX_ITERATIONS": ("agent", "max_iterations"),
    "LEMA_DEVELOPER_MODE": ("agent", "developer_mode"),
    "LEMA_SHELL_TIMEOUT": ("tools", "shell_timeout"),
    "LEMA_LOG_LEVEL": ("logging", "level"),
}


def _apply_env(config: Config) -> None:
    applied = False
    for env_name, (section, key) in _ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        target = getattr(config, section)
        hints = _field_types(target)
        if key not in hints:
            continue
        try:
            setattr(target, key, _coerce(raw, hints[key]))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{env_name}: {exc}") from exc
        applied = True
    if "LEMA_PERMISSIONS" in os.environ:
        config.permissions = PermissionMode.parse(os.environ["LEMA_PERMISSIONS"])
        applied = True
    if os.environ.get("LEMA_DEBUG", "").lower() in {"1", "true", "yes"}:
        config.debug = True
        applied = True
    if applied:
        config.sources.append("environment")


def _normalise_overrides(
    overrides: "dict[str, Any] | Sequence[str] | None",
) -> dict[str, Any]:
    """Accept either a mapping or a list of ``section.key=value`` strings."""
    if overrides is None:
        return {}
    if isinstance(overrides, dict):
        return dict(overrides)
    parsed: dict[str, Any] = {}
    for item in overrides:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            parsed[str(item[0])] = item[1]
            continue
        text = str(item)
        if "=" not in text:
            raise ConfigError(
                f"invalid override {text!r}: expected section.key=value "
                "(for example agent.max_iterations=200)"
            )
        key, _, value = text.partition("=")
        parsed[key.strip()] = value.strip()
    return parsed


def apply_overrides(
    config: Config, overrides: "dict[str, Any] | Sequence[str] | None"
) -> Config:
    """Apply CLI overrides expressed as dotted paths, e.g. ``provider.model``.

    Unknown settings and uncoercible values raise :class:`ConfigError` rather
    than being ignored: a typo in ``--set`` should be reported, not swallowed.
    """
    touched = False
    for dotted, value in _normalise_overrides(overrides).items():
        if value is None:
            continue
        if dotted == "permissions":
            try:
                config.permissions = PermissionMode.parse(value)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            touched = True
            continue
        if dotted in {"debug", "developer_mode"}:
            config.debug = _coerce(value, bool)
            touched = True
            continue

        if "." not in dotted:
            raise ConfigError(
                f"unknown setting {dotted!r}: expected section.key=value "
                f"(sections: {', '.join(sorted(_SECTION_TYPES) + ['provider'])})"
            )
        section_name, key = dotted.split(".", 1)
        target = getattr(config, section_name, None)
        if target is None or not is_dataclass(target):
            raise ConfigError(
                f"unknown setting {dotted!r}: no config section named {section_name!r} "
                f"(sections: {', '.join(sorted(_SECTION_TYPES) + ['provider'])})"
            )
        hints = _field_types(target)
        if key not in hints:
            close = [name for name in hints if name.startswith(key[:3])]
            suggestion = f" (did you mean: {', '.join(sorted(close))}?)" if close else ""
            raise ConfigError(f"unknown setting {dotted!r}{suggestion}")
        try:
            setattr(target, key, _coerce(value, hints[key]))
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"{dotted}: expected {getattr(hints[key], '__name__', hints[key])}, "
                f"got {value!r}"
            ) from exc
        touched = True
    if touched and "cli" not in config.sources:
        config.sources.append("cli")
    return config


#: Backwards-compatible private alias.
_apply_overrides = apply_overrides


def load_config(
    *,
    workspace: Path | None = None,
    cwd: Path | None = None,
    overrides: "dict[str, Any] | Sequence[str] | None" = None,
    provider_name: str | None = None,
    use_global: bool = True,
    use_project: bool = True,
) -> Config:
    """Build the effective configuration for a session.

    ``cwd`` is accepted as an alias for ``workspace``.
    """
    workspace = Path(workspace or cwd or Path.cwd()).resolve()
    project_root = paths.find_project_root(workspace)

    config = Config(workspace=workspace, project_root=project_root)
    config.sources.append("defaults")

    if use_global:
        _load_provider_profiles_dir(config, paths.global_providers_dir())
        _merge_file(config, paths.global_config_file())

    if use_project:
        _load_provider_profiles_dir(config, paths.project_config_dir(project_root) / "providers")
        _merge_file(config, paths.project_config_file(project_root))

    _apply_env(config)
    if overrides:
        apply_overrides(config, overrides)

    # An explicit --profile wins over everything in the files.
    if provider_name:
        _select_profile(config, provider_name)

    # The active profile is always addressable by name.
    config.providers.setdefault(config.provider.name, config.provider)
    return config


def _select_profile(config: Config, name: str) -> None:
    profile = config.providers.get(name)
    if profile is None:
        known = ", ".join(sorted(config.providers)) or "none defined"
        raise ConfigError(
            f"unknown provider profile {name!r} (known profiles: {known}). "
            f"Add {paths.global_providers_dir() / (name + '.toml')} to define it."
        )
    config.provider = profile
    config.sources.append(f"profile:{name}")


DEFAULT_CONFIG_TOML = """\
# Lema Harness configuration
# Docs: https://github.com/lema8/volunteera-OS
#
# Permission modes: unrestricted | ask | readonly | plan
permissions = "ask"

# ---------------------------------------------------------------------------
# Active model. `kind` selects the adapter: openai | anthropic | ollama
# ---------------------------------------------------------------------------
[provider]
kind = "ollama"
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434"
temperature = 0.2
context_window = 32768
# auto = probe for native tool-calling and fall back to a text protocol
tool_mode = "auto"

# ---------------------------------------------------------------------------
# Named profiles. Switch at runtime with `/model <profile>` or `--provider`.
# ---------------------------------------------------------------------------
[providers.ollama]
kind = "ollama"
model = "qwen2.5-coder:7b"
base_url = "http://localhost:11434"
context_window = 32768

[providers.openai]
kind = "openai"
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
context_window = 128000

[providers.openrouter]
kind = "openai"
model = "qwen/qwen-2.5-coder-32b-instruct"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
context_window = 128000

[providers.anthropic]
kind = "anthropic"
model = "claude-sonnet-4-20250514"
base_url = "https://api.anthropic.com/v1"
api_key_env = "ANTHROPIC_API_KEY"
context_window = 200000

# A local OpenAI-compatible server (llama.cpp, vLLM, LM Studio, ...)
[providers.local]
kind = "openai"
model = "local-model"
base_url = "http://localhost:8080/v1"
api_key = "not-needed"
context_window = 16384

[agent]
max_iterations = 100
max_tool_calls_per_turn = 16
parallel_tool_calls = true
subagents_enabled = true
# Must be true before the agent may edit Lema's own source.
developer_mode = false

[context]
compaction_threshold = 0.75
keep_recent_messages = 12
max_tool_result_chars = 16000
auto_compact = true

[skills]
load_builtin = true
max_injected = 4
inject_index = true
keep_history = true

[tools]
shell_timeout = 120.0
shell = "/bin/bash"
web_enabled = true
load_plugins = true
# disabled = ["delete_file"]

# [tools.mcp_servers.filesystem]
# command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]

[logging]
level = "info"
log_sessions = true

[ui]
show_tool_args = true
stream_output = true
banner = true
"""


def default_config_toml() -> str:
    return DEFAULT_CONFIG_TOML


def write_default_global_config(force: bool = False) -> Path:
    """Create ``~/.config/lema/config.toml`` and the companion directories."""
    path = paths.global_config_file()
    paths.ensure_dir(path.parent)
    paths.ensure_dir(paths.global_skills_dir())
    paths.ensure_dir(paths.global_providers_dir())
    paths.ensure_dir(paths.global_tools_dir())
    paths.ensure_dir(paths.history_dir())
    if path.exists() and not force:
        return path
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path


def save_global_config(config: Config) -> Path:
    """Serialise the live config back to the global config file."""
    path = paths.global_config_file()
    paths.ensure_dir(path.parent)

    def clean(d: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None:
                continue
            if isinstance(v, dict):
                nested = clean(v)
                if nested:
                    out[k] = nested
            elif isinstance(v, Path):
                out[k] = str(v)
            else:
                out[k] = v
        return out

    raw = config.to_dict()
    payload = {
        "permissions": raw["permissions"],
        "provider": clean(raw["provider"]),
        "providers": {k: clean(v) for k, v in raw["providers"].items()},
        "agent": clean(raw["agent"]),
        "context": clean(raw["context"]),
        "skills": clean(raw["skills"]),
        "tools": clean(raw["tools"]),
        "logging": clean(raw["logging"]),
        "ui": clean(raw["ui"]),
    }
    with open(path, "wb") as fh:
        tomli_w.dump(payload, fh)
    return path
