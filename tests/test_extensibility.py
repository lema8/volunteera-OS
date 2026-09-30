"""Plugins, MCP readiness, subagents and the tool registry contract.

Spec §22/§23: the architecture must accept new capabilities without the loop
knowing about them, and must not fake concurrency.
"""

from __future__ import annotations

import asyncio

import pytest

from lema.agent.subagent import TaskTool
from lema.tools import COMMON_ALIASES, build_default_registry
from lema.tools.base import (
    FailureKind,
    FunctionTool,
    Tool,
    ToolCategory,
    ToolError,
    ToolResult,
)
from lema.tools.mcp import MCPServer, MCPTool
from lema.tools.plugins import load_plugins, tool as tool_decorator
from lema.tools.registry import ToolRegistry
from scripted import ScriptedProvider, call, calls, text

# --------------------------------------------------------------- registry


def test_registry_is_generic_not_hard_coded(tools):
    """Every tool is described by the same metadata contract."""
    for spec in tools.specs():
        assert spec.name
        assert spec.description
        assert spec.parameters["type"] == "object"
        assert isinstance(spec.parameters.get("properties", {}), dict)


def test_registry_reports_categories(tools):
    categories = {tool.category for tool in tools.all()}
    assert ToolCategory.FILESYSTEM in categories
    assert ToolCategory.SHELL in categories
    assert ToolCategory.SKILL in categories
    assert ToolCategory.GIT in categories
    assert ToolCategory.PROCESS in categories
    assert ToolCategory.SEARCH in categories


@pytest.mark.asyncio
async def test_register_and_dispatch_a_new_tool(tools, tool_context):
    class Greeter(Tool):
        name = "greet"
        description = "Say hello to someone."
        category = ToolCategory.OTHER
        parameters = {
            "type": "object",
            "properties": {"who": {"type": "string"}},
            "required": ["who"],
        }

        async def run(self, ctx, who: str) -> ToolResult:
            return ToolResult.success(f"hello {who}")

    tools.register(Greeter())
    assert "greet" in tools.names()
    result = await tools.dispatch("greet", {"who": "world"}, tool_context)
    assert result.output == "hello world"


def test_registry_revision_changes_when_tools_change(tools):
    before = tools.revision

    class Noop(Tool):
        name = "noop"
        description = "Does nothing at all, quietly."
        parameters = {"type": "object", "properties": {}}

        async def run(self, ctx) -> ToolResult:
            return ToolResult.success("ok")

    tools.register(Noop())
    assert tools.revision != before


def test_duplicate_registration_replaces(tools):
    count = len(tools)

    class Fake(Tool):
        name = "read_file"
        description = "Replacement reader."
        parameters = {"type": "object", "properties": {}}

        async def run(self, ctx) -> ToolResult:
            return ToolResult.success("replaced")

    tools.register(Fake(), replace=True)
    assert len(tools) == count
    assert tools.get("read_file").description == "Replacement reader."


@pytest.mark.asyncio
async def test_function_tool_wraps_a_plain_callable(tools, tool_context):
    async def add(ctx, a: int, b: int) -> ToolResult:
        return ToolResult.success(str(a + b))

    tools.register(
        FunctionTool(
            name="add",
            description="Add two integers together.",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
            handler=add,
        )
    )
    result = await tools.dispatch("add", {"a": 2, "b": 3}, tool_context)
    assert result.output == "5"


@pytest.mark.asyncio
async def test_a_crashing_tool_does_not_crash_the_harness(tools, tool_context):
    class Exploding(Tool):
        name = "explode"
        description = "Raises an unexpected exception."
        parameters = {"type": "object", "properties": {}}

        async def run(self, ctx) -> ToolResult:
            raise RuntimeError("kaboom")

    tools.register(Exploding())
    result = await tools.dispatch("explode", {}, tool_context)
    assert not result.ok
    assert "kaboom" in result.error
    assert result.failure_kind is FailureKind.UNKNOWN


@pytest.mark.asyncio
async def test_tool_error_carries_a_recovery_hint(tools, tool_context):
    class Picky(Tool):
        name = "picky"
        description = "Always fails with advice."
        parameters = {"type": "object", "properties": {}}

        async def run(self, ctx) -> ToolResult:
            raise ToolError(
                "the widget is not calibrated",
                FailureKind.CONFIGURATION,
                hint="Run `widget calibrate` first.",
            )

    tools.register(Picky())
    result = await tools.dispatch("picky", {}, tool_context)
    assert result.failure_kind is FailureKind.CONFIGURATION
    assert "calibrate" in result.hint
    assert "HINT" in result.render_for_model(4000)


def test_aliases_resolve(tools):
    for alias, target in COMMON_ALIASES.items():
        if target in tools.names():
            assert tools.get(alias) is not None


def test_disabled_tools_are_not_registered(config):
    registry = build_default_registry(
        include_web=False, disabled=["run_command", "delete_file"]
    )
    assert "run_command" not in registry.names()
    assert "delete_file" not in registry.names()
    assert "read_file" in registry.names()


def test_allowlist_restricts_the_registry(config):
    registry = build_default_registry(
        include_web=False, enabled=["read_file", "list_directory"]
    )
    assert set(registry.names()) <= {"read_file", "list_directory", "cat", "ls"}
    assert "run_command" not in registry.names()


def test_describe_produces_readable_help(tools):
    described = tools.describe()
    assert "read_file" in described
    assert "run_command" in described


# ---------------------------------------------------------------- plugins


PLUGIN_SOURCE = '''
from lema.tools.base import Tool, ToolCategory, ToolResult


class ShoutTool(Tool):
    name = "shout"
    description = "Uppercase a phrase loudly."
    category = ToolCategory.OTHER
    parameters = {
        "type": "object",
        "properties": {"phrase": {"type": "string"}},
        "required": ["phrase"],
    }

    async def run(self, ctx, phrase: str) -> ToolResult:
        return ToolResult.success(phrase.upper() + "!")


TOOLS = [ShoutTool()]
'''


@pytest.mark.asyncio
async def test_project_plugins_are_loaded(tools, tool_context, workspace):
    plugin_dir = workspace / ".lema" / "tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "shout.py").write_text(PLUGIN_SOURCE)

    result = load_plugins(tools, [plugin_dir])
    assert result.errors == []
    assert any("shout" in name for name in result.loaded)

    call_result = await tools.dispatch("shout", {"phrase": "hello"}, tool_context)
    assert call_result.output == "HELLO!"


def test_a_broken_plugin_is_reported_not_fatal(tools, workspace):
    """One bad plugin must not stop the harness from starting."""
    plugin_dir = workspace / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.py").write_text("this is not valid python (((")
    (plugin_dir / "fine.py").write_text(PLUGIN_SOURCE)

    result = load_plugins(tools, [plugin_dir])
    assert any("shout" in name for name in result.loaded)
    assert len(result.errors) == 1
    assert "broken.py" in result.errors[0]
    assert "shout" in tools.names()


def test_missing_plugin_dir_is_silent(tools, tmp_path):
    result = load_plugins(tools, [tmp_path / "nope"])
    assert result.loaded == [] and result.errors == []


@pytest.mark.asyncio
async def test_plugin_decorator_infers_a_schema(tools, tool_context):
    @tool_decorator(description="Multiply two numbers.")
    async def multiply(ctx, a: int, b: int = 2):
        return ToolResult.success(str(a * b))

    from lema.tools.plugins import _tools_from_module
    import types

    module = types.ModuleType("inline_plugin")
    module.multiply = multiply
    found = _tools_from_module(module)
    assert len(found) == 1
    spec = found[0].spec()
    assert spec.parameters["properties"]["a"]["type"] == "integer"
    assert spec.parameters["required"] == ["a"]

    tools.register(found[0])
    result = await tools.dispatch("multiply", {"a": 21}, tool_context)
    assert result.output == "42"


# -------------------------------------------------------------------- MCP


def test_no_mcp_servers_configured_is_fine(config):
    assert config.tools.mcp_servers == {}


def test_mcp_tool_presents_as_an_ordinary_tool():
    """Spec §22: MCP plugs into the same registry, not a special path."""
    server = MCPServer(name="notes", command=["true"])
    mcp_tool = MCPTool(
        server,
        {
            "name": "read_note",
            "description": "Read a note from the notes server.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    )
    assert isinstance(mcp_tool, Tool)
    assert mcp_tool.name == "notes_read_note"
    assert mcp_tool.remote_name == "read_note"

    registry = ToolRegistry()
    registry.register(mcp_tool)
    spec = next(s for s in registry.specs() if s.name == "notes_read_note")
    assert spec.parameters["properties"]["id"]["type"] == "string"
    assert "note" in spec.description.lower()


@pytest.mark.asyncio
async def test_mcp_tool_reports_a_dead_server_cleanly(tool_context):
    server = MCPServer(name="notes", command=["true"])
    mcp_tool = MCPTool(server, {"name": "read_note", "inputSchema": {"type": "object"}})
    result = await mcp_tool.execute(tool_context, {})
    assert not result.ok
    assert result.failure_kind in {
        FailureKind.CONFIGURATION,
        FailureKind.DEPENDENCY,
        FailureKind.UNKNOWN,
    }


# -------------------------------------------------------------- subagents


@pytest.mark.asyncio
async def test_subagent_runs_a_real_nested_loop(config, tools, tool_context, workspace):
    """A subagent is a real agent with its own context - not a prompt trick."""
    inner = ScriptedProvider(
        [
            calls(call("write_file", path="from_subagent.txt", content="done by sub\n")),
            text("Wrote the file as asked."),
        ]
    )
    tools.register(TaskTool(inner, tools), replace=True)

    result = await tools.dispatch(
        "task",
        {"tasks": [{"task": "Create from_subagent.txt containing 'done by sub'."}]},
        tool_context,
    )
    assert result.ok, result.error
    assert (workspace / "from_subagent.txt").read_text() == "done by sub\n"
    assert "Wrote the file" in result.output


@pytest.mark.asyncio
async def test_subagent_depth_is_bounded(config, tools, tool_context):
    config.agent.max_subagent_depth = 1
    tool_context.depth = 1
    tools.register(TaskTool(ScriptedProvider([text("hi")]), tools), replace=True)
    result = await tools.dispatch(
        "task", {"tasks": [{"task": "go deeper"}]}, tool_context
    )
    assert not result.ok
    assert "depth" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_subagents_can_be_disabled(config, tools, tool_context):
    config.agent.subagents_enabled = False
    tools.register(TaskTool(ScriptedProvider([text("hi")]), tools), replace=True)
    result = await tools.dispatch("task", {"tasks": [{"task": "x"}]}, tool_context)
    assert not result.ok
    assert "subagent" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_parallel_tools_really_run_concurrently(tools, tool_context):
    """Spec §23: no fake parallelism - concurrent calls must overlap in time."""

    class Sleeper(Tool):
        name = "sleeper"
        description = "Sleeps for a while."
        parallel_safe = True
        parameters = {"type": "object", "properties": {}}

        async def run(self, ctx) -> ToolResult:
            await asyncio.sleep(0.2)
            return ToolResult.success("slept")

    tools.register(Sleeper())
    loop = asyncio.get_running_loop()
    started = loop.time()
    results = await tools.dispatch_many(
        [("1", "sleeper", {}), ("2", "sleeper", {}), ("3", "sleeper", {})], tool_context
    )
    elapsed = loop.time() - started
    assert all(r.ok for _, r in results)
    assert elapsed < 0.45, f"calls were serialised ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_unsafe_tools_are_serialised(tools, tool_context):
    order: list[str] = []

    class Unsafe(Tool):
        name = "unsafe"
        description = "Must not run concurrently."
        parallel_safe = False
        parameters = {"type": "object", "properties": {"tag": {"type": "string"}}}

        async def run(self, ctx, tag: str) -> ToolResult:
            order.append(f"start-{tag}")
            await asyncio.sleep(0.05)
            order.append(f"end-{tag}")
            return ToolResult.success(tag)

    tools.register(Unsafe())
    await tools.dispatch_many(
        [("1", "unsafe", {"tag": "a"}), ("2", "unsafe", {"tag": "b"})], tool_context
    )
    assert order == ["start-a", "end-a", "start-b", "end-b"]
