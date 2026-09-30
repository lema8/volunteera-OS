"""Typed configuration model for Lema Harness.

Config is layered: built-in defaults < global ``~/.config/lema/config.toml``
< project ``./.lema/config.toml`` < environment variables < CLI flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionMode(str, Enum):
    """How much the harness may do without asking the user."""

    UNRESTRICTED = "unrestricted"
    """No application-level gating. Only the OS/user account restricts the agent."""

    ASK = "ask"
    """Prompt before mutating or executing operations."""

    READONLY = "readonly"
    """Reject every mutating operation (writes, shell, process spawn)."""

    PLAN = "plan"
    """Read-only, but the agent is told to produce a plan instead of acting."""

    @classmethod
    def parse(cls, value: str | PermissionMode) -> "PermissionMode":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        aliases = {
            "yolo": cls.UNRESTRICTED,
            "full": cls.UNRESTRICTED,
            "auto": cls.UNRESTRICTED,
            "prompt": cls.ASK,
            "confirm": cls.ASK,
            "ro": cls.READONLY,
            "read-only": cls.READONLY,
            "planning": cls.PLAN,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:  # pragma: no cover - surfaced to the user
            valid = ", ".join(m.value for m in cls)
            raise ValueError(f"unknown permission mode {value!r} (expected one of: {valid})") from exc


#: Conventional endpoint per adapter kind, used when `base_url` is unset.
DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "llamacpp": "http://localhost:8080/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
}


@dataclass
class ProviderConfig:
    """Everything needed to talk to one model endpoint.

    ``kind`` selects the adapter (``openai``/``anthropic``/``ollama``), which is
    deliberately separate from ``name`` so a user can define several named
    profiles that share an adapter (e.g. ``openrouter`` and ``groq`` are both
    ``openai``-compatible).
    """

    name: str = "ollama"
    kind: str = "ollama"
    model: str = "qwen2.5-coder:7b"
    #: Empty means "use the conventional endpoint for `kind`"; see
    #: :meth:`effective_base_url`.
    base_url: str = ""
    api_key: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.2
    top_p: float | None = None
    max_tokens: int | None = 4096
    context_window: int = 32768
    timeout: float = 300.0
    #: Some small local models cannot do structured tool calls; the harness then
    #: falls back to a text protocol. ``auto`` probes and caches the answer.
    tool_mode: str = "auto"  # auto | native | text
    stream: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    def effective_base_url(self) -> str:
        """The endpoint to call, defaulting per adapter kind.

        Without this, a user who sets ``kind = "openai"`` but no ``base_url``
        would silently inherit Ollama's port.
        """
        if self.base_url:
            return str(self.base_url).rstrip("/")
        return DEFAULT_BASE_URLS.get(self.kind, "http://localhost:11434")

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        # Conventional fallbacks, tried only when nothing explicit was given.
        for env in (f"{self.name.upper().replace('-', '_')}_API_KEY", f"{self.kind.upper()}_API_KEY"):
            value = os.environ.get(env)
            if value:
                return value
        return None


@dataclass
class AgentConfig:
    #: Hard ceiling on agent<->model round trips for one user turn. Generous by
    #: default: real coding tasks routinely need dozens of tool calls.
    max_iterations: int = 100
    #: Cap on tool calls executed per single model response.
    max_tool_calls_per_turn: int = 16
    #: Run independent tool calls from one response concurrently.
    parallel_tool_calls: bool = True
    #: Number of consecutive provider/tool errors before the loop aborts.
    max_consecutive_errors: int = 5
    #: Allow the agent to spawn subagents via the `task` tool.
    subagents_enabled: bool = True
    max_subagent_depth: int = 2
    subagent_max_iterations: int = 40
    #: Gate on modifying Lema's own runtime source.
    developer_mode: bool = False
    system_prompt_append: str = ""


@dataclass
class ContextConfig:
    #: Fraction of the context window at which compaction kicks in.
    compaction_threshold: float = 0.75
    #: How many of the most recent messages are never summarised away.
    keep_recent_messages: int = 12
    #: Truncation budget for a single tool result injected into context.
    max_tool_result_chars: int = 16000
    #: Truncation budget for a single file read.
    max_file_chars: int = 200000
    include_git_status: bool = True
    include_project_info: bool = True
    #: Enable automatic compaction; when False the user must run /compact.
    auto_compact: bool = True


@dataclass
class SkillsConfig:
    #: Include the skills that ship with Lema.
    load_builtin: bool = True
    #: Extra directories to scan for skills.
    extra_dirs: list[str] = field(default_factory=list)
    #: Max skills injected into a single request (keeps the prompt small).
    max_injected: int = 4
    #: Always inject the compact skill index so the agent knows what exists.
    inject_index: bool = True
    #: Minimum keyword score for a skill to be auto-injected.
    relevance_threshold: float = 0.15
    #: Retrieval strategy name; pluggable (`keyword` ships today).
    retrieval: str = "keyword"
    #: Keep a .versions/ history when a skill is updated or deleted.
    keep_history: bool = True


@dataclass
class ToolsConfig:
    #: Tool names to exclude from the registry.
    disabled: list[str] = field(default_factory=list)
    #: If non-empty, only these tools are registered.
    enabled: list[str] = field(default_factory=list)
    shell_timeout: float = 120.0
    shell_max_output_chars: int = 100000
    #: Default shell binary; overridable per call.
    shell: str = "/bin/bash"
    web_enabled: bool = True
    web_provider: str = "duckduckgo"
    web_search_url: str | None = None
    #: Load user Python plugins from config/tools and ./.lema/tools.
    load_plugins: bool = True
    #: MCP servers: {"name": {"command": [...], "env": {...}}}
    mcp_servers: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoggingConfig:
    level: str = "info"
    #: Write JSONL session transcripts under the state dir.
    log_sessions: bool = True
    #: Include full model request/response bodies (still redacted).
    log_model_io: bool = True
    max_session_files: int = 200


@dataclass
class UIConfig:
    theme: str = "default"
    show_tool_args: bool = True
    show_timings: bool = True
    stream_output: bool = True
    banner: bool = True
    #: Colourised output. Disabled automatically when NO_COLOR is set.
    color: bool = True
    #: Characters of tool output echoed to the terminal (the model sees more).
    tool_output_preview_lines: int = 12


@dataclass
class Config:
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    agent: AgentConfig = field(default_factory=AgentConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    permissions: PermissionMode = PermissionMode.ASK
    workspace: Path = field(default_factory=Path.cwd)
    project_root: Path = field(default_factory=Path.cwd)
    debug: bool = False
    #: Provenance, for `/config` output.
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _dataclass_to_dict(self)

    def select_provider(self, name: str) -> ProviderConfig:
        """Switch the active provider profile by name (used by ``/model``)."""
        if name in self.providers:
            return self.providers[name]
        raise KeyError(name)


def _dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        for f in fields(obj):
            out[f.name] = _dataclass_to_dict(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dataclass_to_dict(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    return obj
