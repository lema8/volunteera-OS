"""Built-in tool set assembly.

``build_default_registry`` composes the standard toolbox.  Nothing here is
special-cased by the agent loop: adding a tool is a matter of appending it to
a registry, which is exactly what plugins and MCP servers do at runtime.
"""

from __future__ import annotations

from typing import Sequence

from lema.tools.base import (
    FailureKind,
    FunctionTool,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)
from lema.tools.filesystem import build_filesystem_tools
from lema.tools.git import build_git_tools
from lema.tools.processes import ProcessManager, build_process_tools
from lema.tools.registry import ToolRegistry
from lema.tools.search import build_search_tools
from lema.tools.shell import build_shell_tools
from lema.tools.skills import build_skill_tools
from lema.tools.web import build_web_tools

#: Names models commonly invent; mapped onto the real tool.
COMMON_ALIASES = {
    "bash": "run_command",
    "shell": "run_command",
    "execute_command": "run_command",
    "exec": "run_command",
    "run_shell": "run_command",
    "terminal": "run_command",
    "cat": "read_file",
    "view_file": "read_file",
    "open_file": "read_file",
    "create_file": "write_file",
    "save_file": "write_file",
    "str_replace": "edit_file",
    "replace_in_file": "edit_file",
    "apply_patch": "edit_file",
    "ls": "list_directory",
    "list_files": "list_directory",
    "list_dir": "list_directory",
    "grep": "search_text",
    "ripgrep": "search_text",
    "search": "search_text",
    "glob": "find_files",
    "rm": "delete_file",
    "remove_file": "delete_file",
    "mkdir": "create_directory",
    "mv": "move_file",
    "rename_file": "move_file",
    "cp": "copy_file",
    "skills": "list_skills",
    "add_skill": "create_skill",
    "new_skill": "create_skill",
    "save_skill": "create_skill",
    "edit_skill": "update_skill",
    "improve_skill": "update_skill",
    "fetch": "fetch_url",
    "browse": "fetch_url",
    "search_web": "web_search",
    "google": "web_search",
    "subagent": "task",
    "delegate": "task",
}


def build_default_registry(
    *,
    include_web: bool = True,
    include_git: bool = True,
    include_processes: bool = True,
    disabled: Sequence[str] = (),
    enabled: Sequence[str] = (),
) -> ToolRegistry:
    """Assemble the standard tool registry."""
    registry = ToolRegistry()

    tools: list[Tool] = []
    tools += build_filesystem_tools()
    tools += build_search_tools()
    tools += build_shell_tools()
    tools += build_skill_tools()
    if include_processes:
        tools += build_process_tools()
    if include_git:
        tools += build_git_tools()
    if include_web:
        tools += build_web_tools()

    allow = set(enabled or ())
    deny = set(disabled or ())

    for tool in tools:
        if allow and tool.name not in allow:
            continue
        if tool.name in deny:
            continue
        registry.register(tool, replace=True)

    for alias, target in COMMON_ALIASES.items():
        if target in registry:
            registry.alias(alias, target)

    return registry


__all__ = [
    "COMMON_ALIASES",
    "FailureKind",
    "FunctionTool",
    "ProcessManager",
    "Tool",
    "ToolCategory",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
