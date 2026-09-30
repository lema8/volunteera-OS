"""Permission policy.

Design rules, in order of importance:

1. The user chooses the policy; the harness does not invent its own.
2. ``unrestricted`` means *unrestricted*: no command blocklists, no path
   jails, no "are you sure" nags.  Only the OS and the user account restrict
   the agent.  There are deliberately no hidden vendor-style rules here.
3. ``readonly``/``plan`` are a hard stop on mutation, enforced at dispatch.
4. ``ask`` prompts, and the answer can be scoped to one call, a tool, a whole
   category, or the rest of the session.

The one exception to rule 2 is Lema's *own source tree*, which is protected
unless ``agent.developer_mode`` is enabled - self-extension via skills is
always allowed, rewriting the runtime is not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from lema.config.schema import PermissionMode

if TYPE_CHECKING:  # pragma: no cover
    from lema.tools.base import Tool, ToolContext


class ApprovalScope(str, Enum):
    ONCE = "once"
    TOOL = "tool"
    CATEGORY = "category"
    SESSION = "session"
    DENY = "deny"
    DENY_TOOL = "deny_tool"


@dataclass
class ApprovalRequest:
    tool_name: str
    category: str
    summary: str
    arguments: dict[str, Any]
    mutating: bool
    dangerous: bool


@dataclass
class Decision:
    allowed: bool
    reason: str | None = None
    hint: str | None = None


#: The UI supplies this; returns the scope the user picked.
ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalScope]]


@dataclass
class PermissionManager:
    mode: PermissionMode = PermissionMode.ASK
    approval_callback: ApprovalCallback | None = None
    developer_mode: bool = False
    #: Absolute path of the Lema package, protected outside developer mode.
    runtime_root: Path | None = None
    #: Names/categories the user approved for the rest of the session.
    approved_tools: set[str] = field(default_factory=set)
    approved_categories: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    session_approved: bool = False
    #: Tools that never need approval regardless of mode (pure reads).
    auto_allow_read_only: bool = True

    def __post_init__(self) -> None:
        if self.runtime_root is None:
            import lema

            self.runtime_root = Path(lema.__file__).resolve().parent

    # ----------------------------------------------------------- utilities

    def set_mode(self, mode: PermissionMode | str) -> None:
        self.mode = PermissionMode.parse(mode)
        # Switching modes clears blanket approvals: they were granted under a
        # different policy.
        self.session_approved = False
        self.approved_tools.clear()
        self.approved_categories.clear()

    def reset_session_approvals(self) -> None:
        self.session_approved = False
        self.approved_tools.clear()
        self.approved_categories.clear()
        self.denied_tools.clear()

    def _paths_in_arguments(self, arguments: dict[str, Any]) -> list[Path]:
        keys = (
            "path",
            "file",
            "file_path",
            "source",
            "destination",
            "directory",
            "dest",
            "src",
        )
        out: list[Path] = []
        for key in keys:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    out.append(Path(value).expanduser())
                except (OSError, ValueError):
                    continue
        return out

    def _touches_runtime(self, arguments: dict[str, Any], cwd: Path) -> Path | None:
        """Detect attempts to modify Lema's own package source."""
        if self.runtime_root is None:
            return None
        for raw in self._paths_in_arguments(arguments):
            path = raw if raw.is_absolute() else (cwd / raw)
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                continue
            try:
                resolved.relative_to(self.runtime_root)
            except ValueError:
                continue
            # Skills living inside the package are normal self-extension.
            if "skills" in resolved.parts and resolved.suffix.lower() == ".md":
                continue
            return resolved
        return None

    # -------------------------------------------------------------- policy

    async def check(
        self, tool: "Tool", arguments: dict[str, Any], ctx: "ToolContext"
    ) -> Decision:
        name = tool.name
        category = tool.category.value

        if name in self.denied_tools:
            return Decision(
                False,
                f"{name} was denied by the user for this session",
                hint="Use a different approach, or ask the user to re-enable the tool.",
            )

        # Guard Lema's own runtime regardless of mode (except developer mode).
        if tool.mutating and not self.developer_mode:
            offending = self._touches_runtime(arguments, ctx.cwd)
            if offending is not None:
                return Decision(
                    False,
                    f"refusing to modify Lema's own runtime source: {offending}",
                    hint=(
                        "Core runtime modification requires developer mode "
                        "(`lema --developer-mode`, or set agent.developer_mode = true). "
                        "Self-extension through skills and project tools is always allowed."
                    ),
                )

        if self.mode is PermissionMode.UNRESTRICTED:
            return Decision(True)

        if self.mode in (PermissionMode.READONLY, PermissionMode.PLAN):
            if tool.read_only and not tool.mutating:
                return Decision(True)
            label = self.mode.value
            return Decision(
                False,
                f"{name} is blocked in {label} mode because it can modify state",
                hint=(
                    "You are in read-only mode. Investigate and report, or describe the "
                    "change you would make; do not attempt to apply it."
                    if self.mode is PermissionMode.PLAN
                    else "Ask the user to switch permissions (e.g. `/config permissions ask`)."
                ),
            )

        # ---- ask mode -----------------------------------------------------
        if tool.read_only and not tool.mutating and self.auto_allow_read_only:
            return Decision(True)
        if self.session_approved:
            return Decision(True)
        if name in self.approved_tools or category in self.approved_categories:
            return Decision(True)
        if not (tool.mutating or tool.dangerous):
            return Decision(True)

        if self.approval_callback is None:
            # Non-interactive run under `ask`: fail closed but explain.
            return Decision(
                False,
                f"{name} requires approval but no approval handler is attached",
                hint=(
                    "Re-run with --permissions unrestricted for non-interactive use, "
                    "or use an interactive session."
                ),
            )

        request = ApprovalRequest(
            tool_name=name,
            category=category,
            summary=tool.summarize_call(arguments),
            arguments=arguments,
            mutating=tool.mutating,
            dangerous=tool.dangerous,
        )
        scope = await self.approval_callback(request)

        if scope is ApprovalScope.DENY:
            return Decision(
                False,
                f"user denied {name}",
                hint="The user rejected this action. Ask how they would like to proceed.",
            )
        if scope is ApprovalScope.DENY_TOOL:
            self.denied_tools.add(name)
            return Decision(
                False,
                f"user denied {name} for this session",
                hint="Do not call this tool again in this session.",
            )
        if scope is ApprovalScope.SESSION:
            self.session_approved = True
        elif scope is ApprovalScope.TOOL:
            self.approved_tools.add(name)
        elif scope is ApprovalScope.CATEGORY:
            self.approved_categories.add(category)
        return Decision(True)

    def describe(self) -> str:
        lines = [f"mode: {self.mode.value}"]
        if self.developer_mode:
            lines.append("developer mode: ON (core runtime is writable)")
        if self.session_approved:
            lines.append("session-wide approval: granted")
        if self.approved_tools:
            lines.append("approved tools: " + ", ".join(sorted(self.approved_tools)))
        if self.approved_categories:
            lines.append("approved categories: " + ", ".join(sorted(self.approved_categories)))
        if self.denied_tools:
            lines.append("denied tools: " + ", ".join(sorted(self.denied_tools)))
        return "\n".join(lines)


def default_manager(
    mode: PermissionMode | str = PermissionMode.ASK,
    *,
    developer_mode: bool = False,
    approval_callback: ApprovalCallback | None = None,
) -> PermissionManager:
    manager = PermissionManager(
        mode=PermissionMode.parse(mode),
        approval_callback=approval_callback,
        developer_mode=developer_mode
        or os.environ.get("LEMA_DEVELOPER_MODE", "").lower() in {"1", "true", "yes"},
    )
    return manager
