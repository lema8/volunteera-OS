"""Tool contract.

A tool is a self-describing unit of capability.  The agent loop knows nothing
about any concrete tool: it asks the registry for specs, hands them to the
model, and dispatches whatever the model asks for.  That is what makes the
harness extensible without touching the loop.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from lema.providers.base import ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from lema.agent.permissions import PermissionManager
    from lema.config.schema import Config
    from lema.skills.registry import SkillRegistry
    from lema.storage.logs import SessionLogger
    from lema.tools.processes import ProcessManager
    from lema.tools.registry import ToolRegistry


class FailureKind(str, Enum):
    """Coarse diagnosis of a failure, surfaced to the model so it can recover.

    The model reacts very differently to "the port is in use" versus "you have
    a syntax error", so we classify instead of returning an opaque error.
    """

    NONE = "none"
    TEMPORARY = "temporary"          # network blip, lock contention - retry
    CONFIGURATION = "configuration"  # bad config/flags/env
    DEPENDENCY = "dependency"        # missing package/binary/module
    SYNTAX = "syntax"                # code does not parse/compile
    PERMISSION = "permission"        # EACCES, sudo required, policy denial
    NOT_FOUND = "not_found"          # missing file/path/target
    TIMEOUT = "timeout"
    INVALID_INPUT = "invalid_input"  # the model called the tool wrongly
    TEST_FAILURE = "test_failure"    # tests ran and failed
    DENIED = "denied"                # user or permission mode refused
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ToolCategory(str, Enum):
    FILESYSTEM = "filesystem"
    SEARCH = "search"
    SHELL = "shell"
    PROCESS = "process"
    GIT = "git"
    SKILL = "skill"
    WEB = "web"
    AGENT = "agent"
    OTHER = "other"


@dataclass
class ToolResult:
    """What a tool hands back.

    ``output`` is what the model sees.  ``data`` is structured detail for the
    UI and for programmatic callers (tests, subagents) and is not necessarily
    serialised into the prompt.
    """

    ok: bool = True
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    failure_kind: FailureKind = FailureKind.NONE
    #: Actionable advice rendered alongside the error for the model.
    hint: str | None = None
    duration_ms: float = 0.0
    #: Free-form metadata (files touched, commands run) used by the context
    #: manager and the /diff command.
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: str = "", **kwargs: Any) -> "ToolResult":
        return cls(ok=True, output=output, **kwargs)

    @classmethod
    def failure(
        cls,
        error: str,
        kind: FailureKind = FailureKind.UNKNOWN,
        *,
        hint: str | None = None,
        output: str = "",
        **kwargs: Any,
    ) -> "ToolResult":
        return cls(
            ok=False,
            error=error,
            failure_kind=kind,
            hint=hint,
            output=output,
            **kwargs,
        )

    def render_for_model(self, max_chars: int = 16000) -> str:
        """Serialise into the text the model receives as the tool result."""
        from lema.util.tokens import truncate_middle

        if self.ok:
            body = self.output if self.output.strip() else "(ok, no output)"
            return truncate_middle(body, max_chars, note="tool output truncated")

        parts = [f"ERROR [{self.failure_kind.value}]: {self.error}"]
        if self.hint:
            parts.append(f"HINT: {self.hint}")
        if self.output.strip():
            parts.append("OUTPUT:\n" + self.output)
        return truncate_middle("\n".join(parts), max_chars, note="tool output truncated")


class ToolError(Exception):
    """Raised inside a handler to produce a classified failure."""

    def __init__(
        self,
        message: str,
        kind: FailureKind = FailureKind.UNKNOWN,
        hint: str | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.hint = hint


@dataclass
class ToolContext:
    """Everything a tool may need, injected rather than imported.

    Keeping this explicit avoids global state and makes tools trivially
    testable and reusable by subagents with a narrowed context.
    """

    config: "Config"
    cwd: Path
    project_root: Path
    permissions: "PermissionManager"
    registry: "ToolRegistry | None" = None
    skills: "SkillRegistry | None" = None
    processes: "ProcessManager | None" = None
    logger: "SessionLogger | None" = None
    #: Async callback used by tools to stream progress to the UI.
    on_progress: Callable[[str], Awaitable[None] | None] | None = None
    #: Depth of subagent nesting; 0 is the main agent.
    depth: int = 0
    #: Arbitrary per-session scratch space shared between tools.
    scratch: dict[str, Any] = field(default_factory=dict)

    def resolve_path(self, raw: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve a user/model-supplied path against the working directory."""
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = (self.cwd / path).resolve()
        else:
            path = path.resolve()
        if must_exist and not path.exists():
            raise ToolError(
                f"path does not exist: {path}",
                FailureKind.NOT_FOUND,
                hint="Use list_directory or find_files to locate the correct path.",
            )
        return path


class Tool(ABC):
    """Base class for all tools."""

    #: Unique name exposed to the model.
    name: str = ""
    #: Shown to the model. Be specific: this is the model's only documentation.
    description: str = ""
    #: JSON Schema (object) describing the arguments.
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    category: ToolCategory = ToolCategory.OTHER
    #: True when the tool can change the system; drives permission checks.
    mutating: bool = False
    #: True when the tool can run arbitrary code (shell, processes).
    dangerous: bool = False
    #: Tools marked read-only stay available in readonly/plan mode.
    read_only: bool = False
    #: Independent calls to this tool may be executed concurrently.
    parallel_safe: bool = True

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must define a name")

    @abstractmethod
    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        """Execute the tool. Raise :class:`ToolError` for classified failures."""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description.strip(),
            parameters=self.parameters,
        )

    def summarize_call(self, arguments: dict[str, Any]) -> str:
        """One-line human description of a call, used by the renderer."""
        if not arguments:
            return self.name
        primary = None
        for key in ("path", "file", "command", "query", "pattern", "name", "directory"):
            if key in arguments and isinstance(arguments[key], (str, int)):
                primary = str(arguments[key])
                break
        if primary is None:
            first = next(iter(arguments.values()), "")
            primary = str(first)
        if len(primary) > 90:
            primary = primary[:87] + "..."
        return f"{self.name} {primary}".strip()

    async def execute(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        """Validate, run, time, and normalise errors. Called by the registry."""
        start = time.perf_counter()
        try:
            validated = validate_arguments(self.parameters, arguments, self.name)
        except ToolError as exc:
            return ToolResult.failure(
                str(exc),
                exc.kind,
                hint=exc.hint,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        try:
            result = await self.run(ctx, **validated)
        except ToolError as exc:
            result = ToolResult.failure(str(exc), exc.kind, hint=exc.hint)
        except PermissionError as exc:
            result = ToolResult.failure(
                f"permission denied: {exc}",
                FailureKind.PERMISSION,
                hint="Check file ownership and mode, or run with sufficient privileges.",
            )
        except FileNotFoundError as exc:
            result = ToolResult.failure(f"not found: {exc}", FailureKind.NOT_FOUND)
        except IsADirectoryError as exc:
            result = ToolResult.failure(f"expected a file: {exc}", FailureKind.INVALID_INPUT)
        except TypeError as exc:
            # Almost always the model passing the wrong argument names.
            result = ToolResult.failure(
                f"invalid arguments for {self.name}: {exc}",
                FailureKind.INVALID_INPUT,
                hint=f"Expected schema: {self.parameters}",
            )
        except NotImplementedError as exc:
            result = ToolResult.failure(str(exc), FailureKind.CONFIGURATION)
        except Exception as exc:  # noqa: BLE001 - tools must never crash the loop
            result = ToolResult.failure(
                f"{type(exc).__name__}: {exc}",
                FailureKind.UNKNOWN,
            )
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result


# ---------------------------------------------------------------------------
# Minimal JSON Schema validation.
#
# A full jsonschema dependency is overkill: we only need the subset that tool
# schemas actually use, plus forgiving coercion, because models routinely send
# "3" instead of 3 or a JSON string instead of an array.
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _coerce_scalar(value: Any, expected: str) -> Any:
    if expected == "string":
        if isinstance(value, (int, float, bool)):
            return str(value)
        return value
    if expected == "integer":
        if isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return value
        return value
    if expected == "number":
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return value
        return value
    if expected == "boolean":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return value
    if expected == "array":
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [value]
            return parsed if isinstance(parsed, list) else [parsed]
        return value
    if expected == "object":
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value
            return parsed
        return value
    return value


def validate_arguments(
    schema: dict[str, Any], arguments: dict[str, Any], tool_name: str
) -> dict[str, Any]:
    """Validate and coerce model-supplied arguments against a JSON Schema."""
    if not isinstance(arguments, dict):
        raise ToolError(
            f"{tool_name}: arguments must be a JSON object, got {type(arguments).__name__}",
            FailureKind.INVALID_INPUT,
        )

    properties: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])
    additional = schema.get("additionalProperties", True)

    out: dict[str, Any] = {}
    errors: list[str] = []

    for key, value in arguments.items():
        if key not in properties:
            if additional is False:
                errors.append(f"unexpected argument {key!r}")
            else:
                out[key] = value
            continue
        prop = properties[key] or {}
        expected = prop.get("type")
        if value is None:
            # Treat explicit nulls as "not provided" so defaults apply.
            continue
        if isinstance(expected, str):
            value = _coerce_scalar(value, expected)
            allowed = _TYPE_MAP.get(expected)
            if allowed and not isinstance(value, allowed):
                errors.append(
                    f"{key!r} must be {expected}, got {type(value).__name__} ({value!r})"
                )
                continue
            if expected == "integer" and isinstance(value, bool):
                errors.append(f"{key!r} must be integer, got boolean")
                continue
        enum = prop.get("enum")
        if enum and value not in enum:
            errors.append(f"{key!r} must be one of {enum}, got {value!r}")
            continue
        if expected == "array" and isinstance(value, (list, tuple)):
            item_schema = prop.get("items") or {}
            item_type = item_schema.get("type")
            if isinstance(item_type, str):
                value = [_coerce_scalar(v, item_type) for v in value]
        out[key] = value

    missing = [key for key in required if key not in out or out[key] is None]
    if missing:
        errors.append(f"missing required argument(s): {', '.join(sorted(missing))}")

    if errors:
        raise ToolError(
            f"{tool_name}: " + "; ".join(errors),
            FailureKind.INVALID_INPUT,
            hint=f"Call {tool_name} with this schema: {schema}",
        )

    # Fill declared defaults so handlers can rely on them.
    for key, prop in properties.items():
        if key not in out and isinstance(prop, dict) and "default" in prop:
            out[key] = prop["default"]

    return out


@dataclass
class FunctionTool(Tool):
    """Adapter that turns a plain async function into a :class:`Tool`.

    This is the entry point for user-defined tools and MCP-backed tools.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Callable[..., Any] | None = None
    category: ToolCategory = ToolCategory.OTHER
    mutating: bool = False
    dangerous: bool = False
    read_only: bool = True
    parallel_safe: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FunctionTool requires a name")
        if self.handler is None:
            raise ValueError(f"FunctionTool {self.name!r} requires a handler")

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        assert self.handler is not None
        import inspect

        sig = inspect.signature(self.handler)
        if "ctx" in sig.parameters:
            result = self.handler(ctx=ctx, **kwargs)
        else:
            result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, str):
            return ToolResult.success(result)
        if isinstance(result, dict):
            import json

            return ToolResult.success(json.dumps(result, indent=2, default=str), data=result)
        return ToolResult.success(str(result))
