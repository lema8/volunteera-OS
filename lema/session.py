"""Session assembly.

One place that wires config -> provider -> tools -> skills -> agent, so the
CLI, the one-shot runner and the test suite all build identical runtimes.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from lema.agent.context import ContextManager
from lema.agent.events import EventHandler
from lema.agent.loop import Agent
from lema.agent.permissions import ApprovalCallback, PermissionManager
from lema.agent.subagent import TaskTool
from lema.config.schema import Config, PermissionMode
from lema.providers import create_provider
from lema.providers.base import ModelProvider
from lema.skills.registry import SkillRegistry, build_registry
from lema.storage.logs import SessionLogger, SessionMeta
from lema.tools import build_default_registry
from lema.tools.base import ToolContext
from lema.tools.mcp import MCPManager
from lema.tools.plugins import load_plugins
from lema.tools.processes import ProcessManager
from lema.tools.registry import ToolRegistry
from lema.util import paths


@dataclass
class Session:
    """A fully wired agent runtime."""

    config: Config
    provider: ModelProvider
    tools: ToolRegistry
    skills: SkillRegistry
    context: ContextManager
    tool_context: ToolContext
    permissions: PermissionManager
    processes: ProcessManager
    logger: SessionLogger
    agent: Agent
    mcp: MCPManager = field(default_factory=MCPManager)
    startup_warnings: list[str] = field(default_factory=list)

    async def aclose(self) -> None:
        """Release every resource the session owns."""
        await self.processes.shutdown()
        await self.mcp.shutdown()
        await self.provider.aclose()
        self.logger.close(
            {
                "messages": len(self.context.messages),
                "skills": len(self.skills),
                "compactions": self.context.compactions,
            }
        )

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def switch_provider(self, provider_name: str) -> ModelProvider:
        """Swap the active model profile without rebuilding the session."""
        profile = self.config.select_provider(provider_name)
        new_provider = create_provider(profile)
        old = self.provider
        self.config.provider = profile
        self.provider = new_provider
        self.agent.provider = new_provider
        self.context.invalidate_system_prompt()
        # Refresh the subagent tool so children use the new model too.
        task_tool = self.tools.get("task")
        if isinstance(task_tool, TaskTool):
            task_tool.provider = new_provider
        self._pending_close = old
        return new_provider

    def reload_skills(self) -> int:
        count = self.skills.reload()
        self.context.invalidate_system_prompt()
        return count


async def build_session(
    config: Config,
    *,
    provider: ModelProvider | None = None,
    on_event: EventHandler | None = None,
    approval_callback: ApprovalCallback | None = None,
    connect_mcp: bool = True,
) -> Session:
    """Construct a ready-to-run session from a configuration.

    ``provider`` may be supplied to embed Lema with an already-constructed
    model client (tests do this, and so would a host application).
    """
    warnings: list[str] = []

    if provider is None:
        provider = create_provider(config.provider)

    permissions = PermissionManager(
        mode=config.permissions,
        approval_callback=approval_callback,
        developer_mode=config.agent.developer_mode,
    )

    skills = build_registry(
        config.project_root,
        include_builtin=config.skills.load_builtin,
        extra_dirs=config.skills.extra_dirs,
    )
    if skills.load_errors:
        warnings += [f"skill load: {err}" for err in skills.load_errors]

    tools = build_default_registry(
        include_web=config.tools.web_enabled,
        disabled=config.tools.disabled,
        enabled=config.tools.enabled,
    )

    processes = ProcessManager()

    logger = SessionLogger(
        enabled=config.logging.log_sessions,
        log_model_io=config.logging.log_model_io,
        max_files=config.logging.max_session_files,
    )
    logger.log_session_start(
        SessionMeta(
            id=logger.session_id,
            started_at=0.0,
            cwd=str(config.workspace),
            provider=config.provider.kind,
            model=config.provider.model,
        ),
        {
            "permissions": config.permissions.value,
            "model": config.provider.model,
            "base_url": config.provider.effective_base_url(),
            "max_iterations": config.agent.max_iterations,
            "sources": config.sources,
            "platform": platform.platform(),
        },
    )

    tool_context = ToolContext(
        config=config,
        cwd=config.workspace,
        project_root=config.project_root,
        permissions=permissions,
        registry=tools,
        skills=skills,
        processes=processes,
        logger=logger,
    )

    # Subagents need the provider and registry, so this tool is added here
    # rather than in the default builder.
    if config.agent.subagents_enabled and "task" not in config.tools.disabled:
        tools.register(TaskTool(provider, tools, on_event=on_event), replace=True)
        tools.alias("subagent", "task")
        tools.alias("delegate", "task")

    if config.tools.load_plugins:
        plugin_dirs = [paths.global_tools_dir(), paths.project_tools_dir(config.project_root)]
        result = load_plugins(tools, plugin_dirs)
        warnings += [f"plugin: {err}" for err in result.errors]
        if result.loaded:
            logger.log_event("plugins_loaded", {"tools": result.loaded})

    mcp = MCPManager()
    if connect_mcp and config.tools.mcp_servers:
        registered, errors = await mcp.connect_all(
            config.tools.mcp_servers, tools, cwd=str(config.workspace)
        )
        warnings += [f"mcp: {err}" for err in errors]
        if registered:
            logger.log_event("mcp_connected", {"tools": registered})

    context = ContextManager(config, skills, cwd=config.workspace)

    agent = Agent(
        config,
        provider,
        tools,
        context,
        tool_context,
        on_event=on_event,
    )

    return Session(
        config=config,
        provider=provider,
        tools=tools,
        skills=skills,
        context=context,
        tool_context=tool_context,
        permissions=permissions,
        processes=processes,
        logger=logger,
        agent=agent,
        mcp=mcp,
        startup_warnings=warnings,
    )
