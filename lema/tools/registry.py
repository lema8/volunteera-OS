"""Tool registry and dispatcher.

The registry is the single place where the agent discovers capabilities.
Tools can be added at any time (including mid-session by a plugin or MCP
connection) and become immediately visible to the model on the next request.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Sequence

from lema.providers.base import ToolSpec
from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
)


class ToolRegistry:
    """A mutable collection of tools, keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._aliases: dict[str, str] = {}
        #: Bumped whenever the set of tools changes; the agent uses this to
        #: know it must re-send tool specs.
        self.revision: int = 0

    # ------------------------------------------------------------ mutation

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        self.revision += 1

    def register_all(self, tools: Iterable[Tool], *, replace: bool = False) -> None:
        for tool in tools:
            self.register(tool, replace=replace)

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            self.revision += 1
            return True
        return False

    def alias(self, alias: str, target: str) -> None:
        """Accept an alternative name (models often invent close variants)."""
        self._aliases[alias] = target

    # ------------------------------------------------------------- lookup

    def get(self, name: str) -> Tool | None:
        if name in self._tools:
            return self._tools[name]
        target = self._aliases.get(name)
        if target:
            return self._tools.get(target)
        # Be forgiving about case and separators.
        normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
        for key, tool in self._tools.items():
            if key.lower() == normalized:
                return tool
        return None

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]

    def by_category(self) -> dict[ToolCategory, list[Tool]]:
        out: dict[ToolCategory, list[Tool]] = {}
        for tool in self.all():
            out.setdefault(tool.category, []).append(tool)
        return out

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        """Closest known tool names, for error messages."""
        import difflib

        return difflib.get_close_matches(name, self.names(), n=limit, cutoff=0.5)

    # -------------------------------------------------------------- specs

    def specs(self, *, allowed: Sequence[str] | None = None) -> list[ToolSpec]:
        """Tool specs to hand to the model, optionally filtered."""
        tools = self.all()
        if allowed is not None:
            allow = set(allowed)
            tools = [t for t in tools if t.name in allow]
        return [t.spec() for t in tools]

    # ----------------------------------------------------------- dispatch

    async def dispatch(
        self, name: str, arguments: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        """Execute one tool call, including permission checks."""
        tool = self.get(name)
        if tool is None:
            suggestions = self.suggest(name)
            hint = (
                f"Did you mean: {', '.join(suggestions)}?"
                if suggestions
                else f"Available tools: {', '.join(self.names())}"
            )
            return ToolResult.failure(
                f"unknown tool {name!r}",
                FailureKind.INVALID_INPUT,
                hint=hint,
            )

        decision = await ctx.permissions.check(tool, arguments, ctx)
        if not decision.allowed:
            return ToolResult.failure(
                decision.reason or f"{tool.name} was not permitted",
                FailureKind.DENIED,
                hint=decision.hint,
            )

        if ctx.logger is not None:
            ctx.logger.log_tool_call(tool.name, arguments, depth=ctx.depth)

        result = await tool.execute(ctx, arguments)

        if ctx.logger is not None:
            ctx.logger.log_tool_result(tool.name, result, depth=ctx.depth)
        return result

    async def dispatch_many(
        self,
        calls: Sequence[tuple[str, str, dict[str, Any]]],
        ctx: ToolContext,
        *,
        parallel: bool = True,
    ) -> list[tuple[str, ToolResult]]:
        """Execute several calls, in parallel when all of them allow it.

        ``calls`` is a sequence of ``(call_id, tool_name, arguments)``.
        Returns ``(call_id, result)`` in the original order.
        """
        if not calls:
            return []

        can_parallel = parallel and len(calls) > 1
        if can_parallel:
            for _, name, _ in calls:
                tool = self.get(name)
                # Unknown tools are "safe" (they just error), but any tool that
                # is not parallel-safe forces sequential execution, because
                # mutations may depend on ordering.
                if tool is not None and not tool.parallel_safe:
                    can_parallel = False
                    break

        if not can_parallel:
            out: list[tuple[str, ToolResult]] = []
            for call_id, name, arguments in calls:
                out.append((call_id, await self.dispatch(name, arguments, ctx)))
            return out

        results = await asyncio.gather(
            *(self.dispatch(name, arguments, ctx) for _, name, arguments in calls),
            return_exceptions=True,
        )
        out = []
        for (call_id, name, _), result in zip(calls, results):
            if isinstance(result, BaseException):
                result = ToolResult.failure(
                    f"{type(result).__name__}: {result}", FailureKind.UNKNOWN
                )
            out.append((call_id, result))
        return out

    def describe(self) -> str:
        """Human-readable inventory, used by ``/tools``."""
        lines: list[str] = []
        for category, tools in sorted(self.by_category().items(), key=lambda kv: kv[0].value):
            lines.append(f"{category.value}:")
            for tool in tools:
                flags = []
                if tool.mutating:
                    flags.append("mutating")
                if tool.dangerous:
                    flags.append("dangerous")
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                summary = tool.description.strip().splitlines()[0] if tool.description else ""
                lines.append(f"  {tool.name:<20} {summary}{suffix}")
        return "\n".join(lines)
