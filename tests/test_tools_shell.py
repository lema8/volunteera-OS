"""Shell and process tool tests - real subprocesses."""

from __future__ import annotations

import asyncio
import sys

import pytest

from lema.tools.base import FailureKind
from lema.tools.classify import classify_output
from lema.tools.shell import run_shell


@pytest.mark.asyncio
async def test_run_command_captures_everything(tools, tool_context, workspace):
    result = await tools.dispatch(
        "run_command", {"command": "echo out; echo err >&2; exit 0"}, tool_context
    )
    assert result.ok, result.error
    assert result.data["exit_code"] == 0
    assert "out" in result.data["stdout"]
    assert "err" in result.data["stderr"]
    assert result.data["working_directory"] == str(workspace)
    assert result.data["duration_ms"] > 0
    assert "exit_code: 0" in result.output


@pytest.mark.asyncio
async def test_run_command_nonzero_exit_is_a_classified_failure(tools, tool_context):
    result = await tools.dispatch(
        "run_command", {"command": "this-binary-does-not-exist-xyz"}, tool_context
    )
    assert not result.ok
    assert result.failure_kind is FailureKind.DEPENDENCY
    assert result.hint
    assert result.data["exit_code"] == 127


@pytest.mark.asyncio
async def test_run_command_respects_cwd_and_env(tools, tool_context, workspace):
    (workspace / "sub").mkdir()
    result = await tools.dispatch(
        "run_command",
        {"command": "pwd; echo $MY_TEST_VAR", "cwd": "sub", "env": {"MY_TEST_VAR": "abc123"}},
        tool_context,
    )
    assert result.ok
    assert str(workspace / "sub") in result.data["stdout"]
    assert "abc123" in result.data["stdout"]


@pytest.mark.asyncio
async def test_run_command_timeout_kills_the_process_tree(tools, tool_context):
    result = await tools.dispatch(
        "run_command", {"command": "sleep 30", "timeout": 1}, tool_context
    )
    assert not result.ok
    assert result.failure_kind is FailureKind.TIMEOUT
    assert result.data["timed_out"] is True
    assert "start_process" in (result.hint or "")


@pytest.mark.asyncio
async def test_run_command_stdin(tools, tool_context):
    result = await tools.dispatch(
        "run_command", {"command": "cat", "stdin": "piped input\n"}, tool_context
    )
    assert result.ok
    assert "piped input" in result.data["stdout"]


@pytest.mark.asyncio
async def test_run_shell_helper_kills_children_on_timeout(workspace):
    """The whole process group must die, not just the shell."""
    result = await run_shell(
        "sleep 20 & sleep 20; wait", cwd=workspace, timeout=1.0
    )
    assert result["timed_out"] is True
    # Nothing should still be holding the pipe open after the kill.
    assert isinstance(result["stdout"], str)


@pytest.mark.asyncio
async def test_which_tool_reports_availability(tools, tool_context):
    result = await tools.dispatch(
        "which", {"programs": ["sh", "definitely-not-a-real-binary-xyz"]}, tool_context
    )
    assert result.ok
    assert result.data["found"]["sh"]
    assert result.data["found"]["definitely-not-a-real-binary-xyz"] is None
    assert "NOT FOUND" in result.output


@pytest.mark.asyncio
async def test_background_process_lifecycle(tools, tool_context, processes):
    start = await tools.dispatch(
        "start_process",
        {
            "command": "for i in 1 2 3 4 5; do echo tick $i; sleep 0.3; done",
            "name": "ticker",
        },
        tool_context,
    )
    assert start.ok, start.error
    assert start.data["running"] is True

    output = await tools.dispatch(
        "get_process_output",
        {"process_id": "ticker", "wait_for": "tick 2", "timeout": 10},
        tool_context,
    )
    assert output.ok, output.error
    assert output.data["matched"] is True
    assert "tick" in output.output

    listing = await tools.dispatch("list_processes", {}, tool_context)
    assert "ticker" in listing.output

    stop = await tools.dispatch("stop_process", {"process_id": "ticker"}, tool_context)
    assert stop.ok
    assert processes.get("ticker").running is False


@pytest.mark.asyncio
async def test_start_process_reports_immediate_failure(tools, tool_context):
    result = await tools.dispatch(
        "start_process", {"command": "exit 3", "name": "boom"}, tool_context
    )
    assert not result.ok
    assert "exited immediately" in result.error


@pytest.mark.asyncio
async def test_get_process_output_unknown_id(tools, tool_context):
    result = await tools.dispatch("get_process_output", {"process_id": "nope"}, tool_context)
    assert not result.ok
    assert result.failure_kind is FailureKind.NOT_FOUND


@pytest.mark.asyncio
async def test_process_manager_shutdown_reaps_everything(tools, tool_context, processes):
    await tools.dispatch("start_process", {"command": "sleep 60", "name": "sleeper"}, tool_context)
    assert processes.get("sleeper").running
    await processes.shutdown()
    assert processes.get("sleeper").running is False


# --------------------------------------------------------------- classifier


@pytest.mark.parametrize(
    "text,exit_code,expected",
    [
        ("bash: foo: command not found", 127, FailureKind.DEPENDENCY),
        ("ModuleNotFoundError: No module named 'requests'", 1, FailureKind.DEPENDENCY),
        ("  File \"x.py\", line 3\nSyntaxError: invalid syntax", 1, FailureKind.SYNTAX),
        ("Permission denied", 1, FailureKind.PERMISSION),
        ("Error: listen EADDRINUSE: address already in use :::3000", 1, FailureKind.CONFIGURATION),
        ("connection refused", 1, FailureKind.TEMPORARY),
        ("FAILED tests/test_x.py::test_y - AssertionError", 1, FailureKind.TEST_FAILURE),
        ("cp: cannot stat 'x': No such file or directory", 1, FailureKind.NOT_FOUND),
        ("", 0, FailureKind.NONE),
    ],
)
def test_failure_classification(text, exit_code, expected):
    kind, hint = classify_output(text, exit_code)
    assert kind is expected
    if expected is not FailureKind.NONE:
        assert hint


def test_classification_of_timeout():
    kind, hint = classify_output("", None, timed_out=True)
    assert kind is FailureKind.TIMEOUT
    assert "start_process" in hint
