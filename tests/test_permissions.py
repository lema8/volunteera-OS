"""Permission model tests.

The central guarantee under test: `unrestricted` really is unrestricted, and
the only thing the harness protects unconditionally is its own runtime source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import lema
from lema.agent.permissions import (
    ApprovalRequest,
    ApprovalScope,
    PermissionManager,
)
from lema.config.schema import PermissionMode
from lema.tools.base import FailureKind


def test_permission_mode_parsing():
    assert PermissionMode.parse("yolo") is PermissionMode.UNRESTRICTED
    assert PermissionMode.parse("ASK") is PermissionMode.ASK
    assert PermissionMode.parse("read-only") is PermissionMode.READONLY
    assert PermissionMode.parse("plan") is PermissionMode.PLAN
    with pytest.raises(ValueError):
        PermissionMode.parse("whatever")


@pytest.mark.asyncio
async def test_unrestricted_mode_never_blocks_or_prompts(tools, tool_context, workspace):
    """No blocklists, no path jails, no prompts - spec §19."""
    prompted = []

    async def approval(request):
        prompted.append(request)
        return ApprovalScope.DENY

    tool_context.permissions = PermissionManager(
        mode=PermissionMode.UNRESTRICTED, approval_callback=approval
    )

    # Commands that a vendor-restricted agent would typically refuse.
    for command in ("rm -rf /tmp/lema-test-dir-xyz", "curl --version", "sudo -n true"):
        result = await tools.dispatch("run_command", {"command": command}, tool_context)
        assert result.failure_kind is not FailureKind.DENIED, command

    # Writing outside the workspace is allowed too.
    outside = Path("/tmp/lema-permission-test.txt")
    result = await tools.dispatch(
        "write_file", {"path": str(outside), "content": "ok"}, tool_context
    )
    assert result.ok
    outside.unlink(missing_ok=True)

    assert prompted == [], "unrestricted mode must not prompt"


@pytest.mark.asyncio
async def test_readonly_mode_blocks_every_mutation(tools, tool_context, workspace):
    tool_context.permissions = PermissionManager(mode=PermissionMode.READONLY)

    for name, args in [
        ("write_file", {"path": "x.txt", "content": "x"}),
        ("edit_file", {"path": "x.txt", "old_string": "a", "new_string": "b"}),
        ("delete_file", {"path": "x.txt"}),
        ("run_command", {"command": "echo hi"}),
        ("start_process", {"command": "sleep 1"}),
        ("git_commit", {"message": "nope"}),
        ("create_skill", {"name": "x", "content": "y"}),
    ]:
        result = await tools.dispatch(name, args, tool_context)
        assert not result.ok, name
        assert result.failure_kind is FailureKind.DENIED, name

    assert not (workspace / "x.txt").exists()


@pytest.mark.asyncio
async def test_readonly_mode_allows_reads(tools, tool_context, workspace):
    (workspace / "readable.txt").write_text("content")
    tool_context.permissions = PermissionManager(mode=PermissionMode.READONLY)
    for name, args in [
        ("read_file", {"path": "readable.txt"}),
        ("list_directory", {"path": "."}),
        ("search_text", {"pattern": "content"}),
        ("list_skills", {}),
        ("git_status", {}),
    ]:
        result = await tools.dispatch(name, args, tool_context)
        assert result.failure_kind is not FailureKind.DENIED, name


@pytest.mark.asyncio
async def test_plan_mode_explains_itself(tools, tool_context):
    tool_context.permissions = PermissionManager(mode=PermissionMode.PLAN)
    result = await tools.dispatch("write_file", {"path": "a", "content": "b"}, tool_context)
    assert not result.ok
    assert "read-only mode" in (result.hint or "")


@pytest.mark.asyncio
async def test_ask_mode_scopes(tools, tool_context, workspace):
    answers: list[ApprovalScope] = []
    requests: list[ApprovalRequest] = []

    async def approval(request):
        requests.append(request)
        return answers.pop(0)

    tool_context.permissions = PermissionManager(
        mode=PermissionMode.ASK, approval_callback=approval
    )

    # Reads never prompt.
    await tools.dispatch("list_directory", {"path": "."}, tool_context)
    assert requests == []

    # ONCE: approves one call only.
    answers.append(ApprovalScope.ONCE)
    assert (await tools.dispatch("write_file", {"path": "a.txt", "content": "1"}, tool_context)).ok
    assert len(requests) == 1

    # TOOL: approves every later call of that tool.
    answers.append(ApprovalScope.TOOL)
    assert (await tools.dispatch("write_file", {"path": "b.txt", "content": "2"}, tool_context)).ok
    assert (await tools.dispatch("write_file", {"path": "c.txt", "content": "3"}, tool_context)).ok
    assert len(requests) == 2

    # CATEGORY: approves the rest of the filesystem category.
    answers.append(ApprovalScope.CATEGORY)
    assert (await tools.dispatch("create_directory", {"path": "d"}, tool_context)).ok
    assert (await tools.dispatch("delete_file", {"path": "a.txt"}, tool_context)).ok
    assert len(requests) == 3

    # SESSION: approves everything.
    answers.append(ApprovalScope.SESSION)
    assert (await tools.dispatch("run_command", {"command": "echo x"}, tool_context)).ok
    assert (await tools.dispatch("run_command", {"command": "echo y"}, tool_context)).ok
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_ask_mode_denials(tools, tool_context):
    answers = [ApprovalScope.DENY, ApprovalScope.DENY_TOOL]

    async def approval(request):
        return answers.pop(0)

    tool_context.permissions = PermissionManager(
        mode=PermissionMode.ASK, approval_callback=approval
    )

    first = await tools.dispatch("run_command", {"command": "echo 1"}, tool_context)
    assert not first.ok and first.failure_kind is FailureKind.DENIED

    second = await tools.dispatch("run_command", {"command": "echo 2"}, tool_context)
    assert not second.ok

    # DENY_TOOL sticks without asking again.
    third = await tools.dispatch("run_command", {"command": "echo 3"}, tool_context)
    assert not third.ok
    assert "for this session" in third.error
    assert answers == []


@pytest.mark.asyncio
async def test_ask_mode_without_a_handler_fails_closed_with_advice(tools, tool_context):
    tool_context.permissions = PermissionManager(mode=PermissionMode.ASK, approval_callback=None)
    result = await tools.dispatch("run_command", {"command": "echo x"}, tool_context)
    assert not result.ok
    assert "--permissions unrestricted" in (result.hint or "")


@pytest.mark.asyncio
async def test_core_runtime_is_protected_outside_developer_mode(tools, tool_context):
    """Spec §24: skills yes, rewriting the harness no."""
    runtime_file = Path(lema.__file__).resolve().parent / "agent" / "loop.py"
    tool_context.permissions = PermissionManager(
        mode=PermissionMode.UNRESTRICTED, developer_mode=False
    )
    result = await tools.dispatch(
        "write_file", {"path": str(runtime_file), "content": "# hijacked"}, tool_context
    )
    assert not result.ok
    assert result.failure_kind is FailureKind.DENIED
    assert "developer mode" in (result.hint or "").lower()
    # And the file is untouched.
    assert "class Agent" in runtime_file.read_text()


@pytest.mark.asyncio
async def test_developer_mode_unlocks_the_core(tool_context, tmp_path, monkeypatch):
    fake_runtime = tmp_path / "fake_lema"
    (fake_runtime / "agent").mkdir(parents=True)
    target = fake_runtime / "agent" / "loop.py"
    target.write_text("original")

    manager = PermissionManager(
        mode=PermissionMode.UNRESTRICTED, developer_mode=True, runtime_root=fake_runtime
    )
    tool_context.permissions = manager
    from lema.tools import build_default_registry

    registry = build_default_registry(include_web=False)
    result = await registry.dispatch(
        "write_file", {"path": str(target), "content": "patched"}, tool_context
    )
    assert result.ok
    assert target.read_text() == "patched"


@pytest.mark.asyncio
async def test_skills_inside_the_package_are_still_writable(tool_context, tmp_path):
    """Self-extension must never be blocked by the runtime guard."""
    fake_runtime = tmp_path / "pkg"
    (fake_runtime / "skills" / "builtin" / "x").mkdir(parents=True)
    target = fake_runtime / "skills" / "builtin" / "x" / "SKILL.md"

    manager = PermissionManager(
        mode=PermissionMode.UNRESTRICTED, developer_mode=False, runtime_root=fake_runtime
    )
    tool_context.permissions = manager
    from lema.tools import build_default_registry

    registry = build_default_registry(include_web=False)
    result = await registry.dispatch(
        "write_file", {"path": str(target), "content": "# A skill\n\nbody"}, tool_context
    )
    assert result.ok, result.error


def test_switching_mode_clears_blanket_approvals():
    manager = PermissionManager(mode=PermissionMode.ASK)
    manager.session_approved = True
    manager.approved_tools.add("run_command")
    manager.set_mode(PermissionMode.UNRESTRICTED)
    assert manager.session_approved is False
    assert manager.approved_tools == set()
