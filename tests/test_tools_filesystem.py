"""Filesystem tool tests - real files, real edits, real diffs."""

from __future__ import annotations

from pathlib import Path

import pytest

from lema.tools.base import FailureKind


async def run(tools, ctx, name, **kwargs):
    return await tools.dispatch(name, kwargs, ctx)


@pytest.mark.asyncio
async def test_write_then_read_roundtrip(tools, tool_context, workspace):
    result = await run(tools, tool_context, "write_file", path="a/b/hello.txt", content="hi\nthere\n")
    assert result.ok, result.error
    assert (workspace / "a" / "b" / "hello.txt").read_text() == "hi\nthere\n"
    assert result.data["created"] is True
    assert "Created" in result.output

    read = await run(tools, tool_context, "read_file", path="a/b/hello.txt")
    assert read.ok
    assert "1│hi" in read.output
    assert "2│there" in read.output
    assert read.data["total_lines"] == 2


@pytest.mark.asyncio
async def test_read_file_paging(tools, tool_context, workspace):
    (workspace / "big.txt").write_text("\n".join(f"line{i}" for i in range(1, 101)))
    result = await run(tools, tool_context, "read_file", path="big.txt", offset=10, limit=5)
    assert result.ok
    assert "line10" in result.output
    assert "line14" in result.output
    assert "line15" not in result.output
    assert "Continue with offset=15" in result.output


@pytest.mark.asyncio
async def test_read_missing_file_is_classified(tools, tool_context):
    result = await run(tools, tool_context, "read_file", path="nope.txt")
    assert not result.ok
    assert result.failure_kind is FailureKind.NOT_FOUND
    assert result.hint


@pytest.mark.asyncio
async def test_read_binary_file_is_not_dumped(tools, tool_context, workspace):
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 10)
    result = await run(tools, tool_context, "read_file", path="blob.bin")
    assert result.ok
    assert result.data["binary"] is True
    assert "binary file" in result.output


@pytest.mark.asyncio
async def test_write_file_returns_a_diff_when_overwriting(tools, tool_context, workspace):
    (workspace / "x.py").write_text("a = 1\nb = 2\n")
    result = await run(tools, tool_context, "write_file", path="x.py", content="a = 1\nb = 3\n")
    assert result.ok
    assert "-b = 2" in result.output
    assert "+b = 3" in result.output


@pytest.mark.asyncio
async def test_edit_file_replaces_exactly_once(tools, tool_context, workspace):
    (workspace / "m.py").write_text("def f():\n    return 1\n")
    result = await run(
        tools, tool_context, "edit_file", path="m.py", old_string="return 1", new_string="return 42"
    )
    assert result.ok, result.error
    assert (workspace / "m.py").read_text() == "def f():\n    return 42\n"
    assert result.data["replacements"] == 1
    assert "+    return 42" in result.output


@pytest.mark.asyncio
async def test_edit_file_refuses_ambiguous_match(tools, tool_context, workspace):
    (workspace / "m.py").write_text("x = 1\nx = 1\n")
    result = await run(tools, tool_context, "edit_file", path="m.py", old_string="x = 1", new_string="x = 2")
    assert not result.ok
    assert result.failure_kind is FailureKind.INVALID_INPUT
    assert "appears 2 times" in result.error
    assert "replace_all" in (result.hint or "")
    # Unchanged on failure.
    assert (workspace / "m.py").read_text() == "x = 1\nx = 1\n"


@pytest.mark.asyncio
async def test_edit_file_replace_all(tools, tool_context, workspace):
    (workspace / "m.py").write_text("x = 1\nx = 1\n")
    result = await run(
        tools, tool_context, "edit_file", path="m.py", old_string="x = 1", new_string="x = 2", replace_all=True
    )
    assert result.ok
    assert (workspace / "m.py").read_text() == "x = 2\nx = 2\n"
    assert result.data["replacements"] == 2


@pytest.mark.asyncio
async def test_edit_file_detects_whitespace_mismatch(tools, tool_context, workspace):
    # The file is tab-indented; the model supplies space indentation.
    (workspace / "m.py").write_text("def f():\n\treturn 1\n")
    result = await run(
        tools,
        tool_context,
        "edit_file",
        path="m.py",
        old_string="def f():\n    return 1",
        new_string="def f():\n    return 2",
    )
    assert not result.ok
    assert result.failure_kind is FailureKind.INVALID_INPUT
    assert "indentation/whitespace differs" in (result.hint or "")
    assert (workspace / "m.py").read_text() == "def f():\n\treturn 1\n"


@pytest.mark.asyncio
async def test_list_directory_skips_noise(tools, tool_context, workspace):
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("x")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "junk.js").write_text("x")
    result = await run(tools, tool_context, "list_directory", path=".", depth=2)
    assert result.ok
    assert "src/" in result.output
    assert "main.py" in result.output
    assert "node_modules" not in result.output


@pytest.mark.asyncio
async def test_delete_file_and_recursive_guard(tools, tool_context, workspace):
    (workspace / "gone.txt").write_text("bye")
    result = await run(tools, tool_context, "delete_file", path="gone.txt")
    assert result.ok
    assert not (workspace / "gone.txt").exists()

    tree = workspace / "tree"
    tree.mkdir()
    (tree / "f.txt").write_text("x")
    guarded = await run(tools, tool_context, "delete_file", path="tree")
    assert not guarded.ok
    assert "recursive=true" in (guarded.hint or "")
    assert tree.exists()

    forced = await run(tools, tool_context, "delete_file", path="tree", recursive=True)
    assert forced.ok
    assert not tree.exists()


@pytest.mark.asyncio
async def test_move_and_copy(tools, tool_context, workspace):
    (workspace / "one.txt").write_text("1")
    moved = await run(tools, tool_context, "move_file", source="one.txt", destination="sub/two.txt")
    assert moved.ok
    assert (workspace / "sub" / "two.txt").read_text() == "1"
    assert not (workspace / "one.txt").exists()

    copied = await run(tools, tool_context, "copy_file", source="sub/two.txt", destination="three.txt")
    assert copied.ok
    assert (workspace / "three.txt").read_text() == "1"

    clash = await run(tools, tool_context, "copy_file", source="three.txt", destination="sub/two.txt")
    assert not clash.ok
    assert "overwrite=true" in (clash.hint or "")


@pytest.mark.asyncio
async def test_create_directory_is_idempotent(tools, tool_context, workspace):
    first = await run(tools, tool_context, "create_directory", path="deep/nested/dir")
    assert first.ok and first.data["created"] is True
    second = await run(tools, tool_context, "create_directory", path="deep/nested/dir")
    assert second.ok and second.data["created"] is False


@pytest.mark.asyncio
async def test_touched_files_are_tracked(tools, tool_context, workspace):
    await run(tools, tool_context, "write_file", path="tracked.txt", content="x")
    await run(tools, tool_context, "edit_file", path="tracked.txt", old_string="x", new_string="y")
    touched = tool_context.scratch["touched_files"]
    assert str(workspace / "tracked.txt") in touched


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_with_the_schema(tools, tool_context):
    result = await tools.dispatch("read_file", {"wrong_arg": 1}, tool_context)
    assert not result.ok
    assert result.failure_kind is FailureKind.INVALID_INPUT
    assert "missing required argument" in result.error
    assert "path" in str(result.hint)


@pytest.mark.asyncio
async def test_unknown_tool_suggests_alternatives(tools, tool_context):
    result = await tools.dispatch("read_fil", {}, tool_context)
    assert not result.ok
    assert "read_file" in (result.hint or "")


@pytest.mark.asyncio
async def test_tool_aliases_resolve(tools, tool_context, workspace):
    (workspace / "aliased.txt").write_text("hello")
    result = await tools.dispatch("cat", {"path": "aliased.txt"}, tool_context)
    assert result.ok
    assert "hello" in result.output
