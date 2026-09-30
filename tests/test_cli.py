"""CLI surface: slash commands, subcommands, and the real `lema` binary."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lema.cli.commands import COMMANDS, dispatch
from lema.config.schema import PermissionMode
from scripted import ScriptedProvider, text

LEMA = Path(sys.executable).parent / "lema"


def run_cli(*args, cwd=None, env=None, timeout=90):
    environment = dict(os.environ)
    environment.setdefault("NO_COLOR", "1")
    environment.update(env or {})
    return subprocess.run(
        [str(LEMA), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


# ------------------------------------------------------- the real binary


def test_the_binary_is_installed():
    assert LEMA.exists(), f"{LEMA} is missing - `pip install -e .` first"


def test_version(workspace):
    result = run_cli("--version", cwd=workspace)
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_help_lists_the_subcommands(workspace):
    result = run_cli("--help", cwd=workspace)
    assert result.returncode == 0
    for word in ("--permissions", "--model", "--provider", "task"):
        assert word in result.stdout


def test_doctor_reports_the_environment(workspace):
    result = run_cli("doctor", cwd=workspace)
    combined = result.stdout + result.stderr
    assert "tools" in combined.lower()
    assert "skills" in combined.lower()
    # Ollama is not running in CI, so a non-zero exit is expected; what
    # matters is that the report is produced rather than a traceback.
    assert "Traceback" not in combined


def test_init_creates_the_config(workspace):
    result = run_cli("init", cwd=workspace)
    assert result.returncode == 0, result.stderr
    config_file = Path(os.environ["LEMA_HOME"]) / "config" / "config.toml"
    assert config_file.exists()
    assert "[provider]" in config_file.read_text()


def test_skills_subcommand_lists_builtins(workspace):
    result = run_cli("skills", cwd=workspace)
    assert result.returncode == 0, result.stderr
    assert "debugging" in result.stdout
    assert "writing-skills" in result.stdout


def test_tools_subcommand_lists_the_registry(workspace):
    result = run_cli("tools", cwd=workspace)
    assert result.returncode == 0, result.stderr
    for name in ("read_file", "write_file", "run_command", "create_skill", "git_status"):
        assert name in result.stdout


def test_config_subcommand_shows_effective_settings(workspace):
    result = run_cli("config", cwd=workspace)
    assert result.returncode == 0, result.stderr
    assert "model" in result.stdout.lower()


def test_set_override_reaches_the_config(workspace):
    result = run_cli("config", "--set", "agent.max_iterations=321", cwd=workspace)
    assert result.returncode == 0, result.stderr
    assert "321" in result.stdout


def test_bad_override_is_reported_not_crashed(workspace):
    result = run_cli("config", "--set", "agent.nope=1", cwd=workspace)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "nope" in (result.stdout + result.stderr)


def test_unreachable_provider_fails_gracefully(workspace):
    """No traceback when the model endpoint is down - spec §11."""
    result = run_cli(
        "--provider", "ollama",
        "--base-url", "http://127.0.0.1:9",
        "--print",
        "say hello",
        cwd=workspace,
        timeout=120,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Traceback" not in combined
    assert "127.0.0.1:9" in combined or "cannot reach" in combined.lower()


def test_nonexistent_cwd_is_reported(workspace):
    result = run_cli("--cwd", "/no/such/place", "--print", "hi", cwd=workspace)
    assert result.returncode != 0
    assert "not a directory" in (result.stdout + result.stderr)


# ------------------------------------------------------- slash commands


def test_slash_commands_cover_the_spec():
    """Spec §18 names the commands that must exist."""
    required = {
        "help", "model", "skills", "tools", "context",
        "status", "diff", "clear", "compact", "config", "exit",
    }
    assert required <= set(COMMANDS)


class FakeRepl:
    """The exact surface slash commands use: `.session` and `.renderer`."""

    def __init__(self, session):
        from rich.console import Console

        from lema.cli.renderer import Renderer

        self.buffer = io.StringIO()
        self.session = session
        self.renderer = Renderer(
            session.config,
            Console(file=self.buffer, width=100, no_color=True, highlight=False),
        )

    @property
    def output(self) -> str:
        return self.buffer.getvalue()


@pytest.fixture
async def session(config):
    from lema.session import build_session

    config.ui.banner = False
    provider = ScriptedProvider([text("ok")])
    built = await build_session(config, provider=provider, connect_mcp=False)
    yield built
    await built.aclose()


@pytest.fixture
def repl(session):
    return FakeRepl(session)


async def run_command(repl, line):
    """Dispatch a slash command and return (result, printed output)."""
    before = len(repl.output)
    result = await dispatch(repl, line)
    return result, repl.output[before:]


@pytest.mark.asyncio
async def test_help_lists_every_command(repl):
    result, output = await run_command(repl, "/help")
    assert result.handled
    for name in ("skills", "tools", "compact", "exit"):
        assert name in output


@pytest.mark.asyncio
async def test_unknown_command_suggests_something(repl):
    result, output = await run_command(repl, "/skilz")
    assert result.handled
    assert "/skills" in output


@pytest.mark.asyncio
async def test_slash_commands_never_reach_the_model(repl):
    """Spec §5: slash commands are client-side, not prompts."""
    before = repl.session.provider.call_count
    for command in ("/help", "/tools", "/skills", "/status", "/context", "/config"):
        result, _ = await run_command(repl, command)
        assert result.handled, command
        assert result.forward_to_agent is None, command
    assert repl.session.provider.call_count == before


@pytest.mark.asyncio
async def test_skills_command_shows_the_registry(repl):
    _, output = await run_command(repl, "/skills")
    assert "debugging" in output


@pytest.mark.asyncio
async def test_skills_command_can_show_one_skill(repl):
    _, output = await run_command(repl, "/skills debugging")
    assert "debugging" in output.lower()
    assert len(output) > 200  # the body, not just the index line


@pytest.mark.asyncio
async def test_tools_command_lists_by_category(repl):
    _, output = await run_command(repl, "/tools")
    assert "read_file" in output
    assert "filesystem" in output.lower()


@pytest.mark.asyncio
async def test_context_command_reports_usage(repl):
    _, output = await run_command(repl, "/context")
    assert "tokens" in output.lower()


@pytest.mark.asyncio
async def test_status_command_reports_the_session(repl):
    _, output = await run_command(repl, "/status")
    lowered = output.lower()
    assert "model" in lowered
    assert "permission" in lowered


@pytest.mark.asyncio
async def test_model_command_shows_and_switches(repl):
    _, shown = await run_command(repl, "/model")
    assert repl.session.config.provider.model in shown

    _, switched = await run_command(repl, "/model llama3.2:3b")
    assert repl.session.config.provider.model == "llama3.2:3b"
    assert "llama3.2:3b" in switched


@pytest.mark.asyncio
async def test_clear_empties_the_conversation(repl):
    from lema.providers.base import Message

    repl.session.context.add(Message.user("something"))
    result, _ = await run_command(repl, "/clear")
    assert repl.session.context.messages == []
    assert result.handled


@pytest.mark.asyncio
async def test_compact_command_runs_compaction(repl):
    from lema.providers.base import Message

    for i in range(30):
        repl.session.context.add(Message.user(f"message {i}"))
    result, _ = await run_command(repl, "/compact")
    assert result.handled
    assert len(repl.session.context.messages) < 30


@pytest.mark.asyncio
async def test_diff_command_uses_git(repl, workspace):
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "init"],
        cwd=workspace,
        check=True,
    )
    (workspace / "f.txt").write_text("hello world\n")
    _, output = await run_command(repl, "/diff")
    assert "hello world" in output


@pytest.mark.asyncio
async def test_config_command_prints_settings(repl):
    _, output = await run_command(repl, "/config")
    assert "iterations" in output


@pytest.mark.asyncio
async def test_permissions_can_be_changed_at_runtime(repl):
    result, _ = await run_command(repl, "/permissions readonly")
    assert result.handled
    assert repl.session.config.permissions is PermissionMode.READONLY
    assert repl.session.permissions.mode is PermissionMode.READONLY


@pytest.mark.asyncio
async def test_exit_requests_shutdown(repl):
    result, _ = await run_command(repl, "/exit")
    assert result.exit


@pytest.mark.asyncio
async def test_plain_text_is_not_a_slash_command(repl):
    result, _ = await run_command(repl, "fix the login bug")
    assert not result.handled
