"""Shared test fixtures.

Every test runs against an isolated LEMA_HOME and a temporary workspace, so
the suite never reads or writes the developer's real configuration, skills or
logs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lema.agent.context import ContextManager  # noqa: E402
from lema.agent.loop import Agent  # noqa: E402
from lema.agent.permissions import PermissionManager  # noqa: E402
from lema.config.schema import Config, PermissionMode  # noqa: E402
from lema.skills.registry import build_registry  # noqa: E402
from lema.storage.logs import SessionLogger  # noqa: E402
from lema.tools import build_default_registry  # noqa: E402
from lema.tools.base import ToolContext  # noqa: E402
from lema.tools.processes import ProcessManager  # noqa: E402
from scripted import ScriptedProvider  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Redirect every global Lema path into a throwaway directory."""
    home = tmp_path_factory.mktemp("lema-home")
    monkeypatch.setenv("LEMA_HOME", str(home))
    # Ensure no ambient config leaks in.
    for name in list(os.environ):
        if name.startswith("LEMA_") and name != "LEMA_HOME":
            monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """An empty project directory."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".lema").mkdir()
    return root


@pytest.fixture
def config(workspace: Path) -> Config:
    cfg = Config(workspace=workspace, project_root=workspace)
    cfg.permissions = PermissionMode.UNRESTRICTED
    cfg.provider.context_window = 32768
    cfg.provider.model = "scripted-model"
    cfg.ui.stream_output = False
    cfg.ui.banner = False
    cfg.logging.log_sessions = False
    cfg.tools.web_enabled = False
    cfg.tools.shell_timeout = 30.0
    return cfg


@pytest.fixture
def skills(config: Config):
    return build_registry(config.project_root, include_builtin=False)


@pytest.fixture
def skills_with_builtin(config: Config):
    return build_registry(config.project_root, include_builtin=True)


@pytest.fixture
def permissions(config: Config) -> PermissionManager:
    return PermissionManager(mode=config.permissions, approval_callback=None)


@pytest.fixture
def tools(config: Config):
    return build_default_registry(include_web=False)


@pytest.fixture
def processes():
    return ProcessManager()


@pytest.fixture
def tool_context(config, permissions, tools, skills, processes) -> ToolContext:
    return ToolContext(
        config=config,
        cwd=config.workspace,
        project_root=config.project_root,
        permissions=permissions,
        registry=tools,
        skills=skills,
        processes=processes,
        logger=SessionLogger(enabled=False),
    )


@pytest.fixture
def context(config, skills) -> ContextManager:
    return ContextManager(config, skills, cwd=config.workspace)


@pytest.fixture
def make_agent(config, tools, context, tool_context):
    """Factory: build an Agent around any provider."""

    def _make(provider, *, on_event=None, max_iterations: int | None = None) -> Agent:
        return Agent(
            config,
            provider,
            tools,
            context,
            tool_context,
            on_event=on_event,
            max_iterations=max_iterations,
        )

    return _make


@pytest.fixture
def events():
    """Collects agent events for assertions."""
    collected = []

    async def handler(event):
        collected.append(event)

    handler.collected = collected  # type: ignore[attr-defined]
    return handler
