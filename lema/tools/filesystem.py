"""Filesystem tools.

Design notes:

* ``read_file`` emits line numbers, because every subsequent edit and every
  compiler/test error the agent reads is expressed in line numbers.
* ``edit_file`` is a literal string replacement that *must match exactly once*
  by default.  Line-number-based patching is far more fragile when the model
  is working from a slightly stale view of the file.
* Every mutation returns a unified diff, so the model can confirm the change
  really is what it intended instead of assuming success.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import shutil
from pathlib import Path
from typing import Any

from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)
from lema.util.paths import shorten_path
from lema.util.tokens import truncate_middle

#: Directories that are never interesting to list or walk.
NOISE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".tox",
    ".gradle",
    ".idea",
    ".svelte-kit",
    "coverage",
    ".terraform",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class",
    ".pyc", ".pyo", ".wasm", ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".sqlite", ".db",
}


def is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(8192)
    except OSError:
        return False
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    # Heuristic: a high ratio of non-text bytes.
    text_bytes = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    nontext = sum(1 for byte in chunk if byte not in text_bytes)
    return nontext / len(chunk) > 0.30


def read_text(path: Path, max_chars: int | None = None) -> str:
    data = path.read_bytes()
    if max_chars is not None and len(data) > max_chars * 4:
        data = data[: max_chars * 4]
    return data.decode("utf-8", errors="replace")


def make_diff(before: str, after: str, path: str, context: int = 3) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(diff)


def _record_change(ctx: ToolContext, path: Path, action: str) -> None:
    """Track touched files so /diff and the context manager can report them."""
    touched = ctx.scratch.setdefault("touched_files", {})
    touched[str(path)] = action


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file and return its contents with line numbers. "
        "Use `offset` and `limit` to page through large files. "
        "Always read a file before editing it."
    )
    category = ToolCategory.FILESYSTEM
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, absolute or relative to the working directory."},
            "offset": {"type": "integer", "description": "1-based line to start from.", "default": 1},
            "limit": {"type": "integer", "description": "Maximum number of lines to return.", "default": 2000},
            "line_numbers": {"type": "boolean", "description": "Prefix each line with its number.", "default": True},
        },
        "required": ["path"],
    }

    async def run(
        self,
        ctx: ToolContext,
        path: str,
        offset: int = 1,
        limit: int = 2000,
        line_numbers: bool = True,
    ) -> ToolResult:
        target = ctx.resolve_path(path)
        if not target.exists():
            raise ToolError(
                f"file does not exist: {target}",
                FailureKind.NOT_FOUND,
                hint="Use find_files or list_directory to locate it.",
            )
        if target.is_dir():
            raise ToolError(
                f"{target} is a directory",
                FailureKind.INVALID_INPUT,
                hint="Use list_directory for directories.",
            )
        if is_probably_binary(target):
            size = target.stat().st_size
            return ToolResult.success(
                f"(binary file, {size} bytes, not shown: {target})",
                data={"path": str(target), "binary": True, "size": size},
            )

        content = read_text(target, ctx.config.context.max_file_chars)
        lines = content.splitlines()
        total = len(lines)
        start = max(1, int(offset)) - 1
        end = min(total, start + max(1, int(limit)))
        selected = lines[start:end]

        if line_numbers:
            width = len(str(end)) if end else 1
            body = "\n".join(
                f"{str(start + i + 1).rjust(width)}\u2502{line}"
                for i, line in enumerate(selected)
            )
        else:
            body = "\n".join(selected)

        header = f"{shorten_path(target, ctx.cwd)} (lines {start + 1}-{end} of {total})"
        note = ""
        if end < total:
            note = f"\n\n[{total - end} more lines. Continue with offset={end + 1}.]"

        return ToolResult.success(
            f"{header}\n{body}{note}",
            data={
                "path": str(target),
                "total_lines": total,
                "start": start + 1,
                "end": end,
                "content": "\n".join(selected),
            },
        )


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write text to a file, creating parent directories as needed. "
        "Overwrites the whole file - prefer edit_file for targeted changes to existing files. "
        "Returns a diff of what changed."
    )
    category = ToolCategory.FILESYSTEM
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path."},
            "content": {"type": "string", "description": "Full file contents to write."},
            "create_parents": {"type": "boolean", "description": "Create missing parent directories.", "default": True},
            "overwrite": {"type": "boolean", "description": "Allow replacing an existing file.", "default": True},
        },
        "required": ["path", "content"],
    }

    async def run(
        self,
        ctx: ToolContext,
        path: str,
        content: str,
        create_parents: bool = True,
        overwrite: bool = True,
    ) -> ToolResult:
        target = ctx.resolve_path(path)
        existed = target.exists()
        if existed and not overwrite:
            raise ToolError(
                f"{target} already exists and overwrite=false",
                FailureKind.INVALID_INPUT,
                hint="Pass overwrite=true, or use edit_file to modify it in place.",
            )
        if target.is_dir():
            raise ToolError(f"{target} is a directory", FailureKind.INVALID_INPUT)

        before = ""
        if existed:
            try:
                before = read_text(target)
            except OSError:
                before = ""

        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.is_dir():
            raise ToolError(
                f"parent directory does not exist: {target.parent}",
                FailureKind.NOT_FOUND,
                hint="Pass create_parents=true or create the directory first.",
            )

        target.write_text(content, encoding="utf-8")
        _record_change(ctx, target, "modified" if existed else "created")

        rel = shorten_path(target, ctx.cwd)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if existed and before != content:
            diff = make_diff(before, content, rel)
            body = f"Updated {rel} ({line_count} lines)\n{truncate_middle(diff, 6000, 'diff truncated')}"
        elif existed:
            body = f"{rel} is unchanged (content identical)"
        else:
            body = f"Created {rel} ({line_count} lines)"

        return ToolResult.success(
            body,
            data={
                "path": str(target),
                "created": not existed,
                "bytes": len(content.encode("utf-8")),
                "lines": line_count,
            },
            metadata={"touched": [str(target)]},
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file. `old_string` must appear exactly once "
        "unless replace_all=true - include enough surrounding context to make it unique. "
        "Returns a diff. This is the preferred way to modify existing files."
    )
    category = ToolCategory.FILESYSTEM
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_string": {"type": "string", "description": "Exact text to find, including whitespace and indentation."},
            "new_string": {"type": "string", "description": "Replacement text. Use an empty string to delete."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring exactly one.", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    async def run(
        self,
        ctx: ToolContext,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        target = ctx.resolve_path(path)
        if not target.exists():
            raise ToolError(
                f"file does not exist: {target}",
                FailureKind.NOT_FOUND,
                hint="Use write_file to create a new file.",
            )
        if target.is_dir():
            raise ToolError(f"{target} is a directory", FailureKind.INVALID_INPUT)

        before = read_text(target)
        if old_string == new_string:
            raise ToolError(
                "old_string and new_string are identical; nothing to do",
                FailureKind.INVALID_INPUT,
            )

        count = before.count(old_string)
        if count == 0:
            hint = "Read the file again - your `old_string` does not match the current contents."
            # Whitespace mismatches are by far the most common cause; say so.
            stripped = "\n".join(line.strip() for line in old_string.splitlines())
            haystack = "\n".join(line.strip() for line in before.splitlines())
            if stripped and stripped in haystack:
                hint = (
                    "The text exists but the indentation/whitespace differs. "
                    "Re-read the file and copy the exact leading whitespace."
                )
            raise ToolError(
                f"old_string not found in {shorten_path(target, ctx.cwd)}",
                FailureKind.INVALID_INPUT,
                hint=hint,
            )
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string appears {count} times in {shorten_path(target, ctx.cwd)}",
                FailureKind.INVALID_INPUT,
                hint=(
                    "Add more surrounding context so the match is unique, "
                    "or pass replace_all=true to change all occurrences."
                ),
            )

        after = (
            before.replace(old_string, new_string)
            if replace_all
            else before.replace(old_string, new_string, 1)
        )
        target.write_text(after, encoding="utf-8")
        _record_change(ctx, target, "modified")

        rel = shorten_path(target, ctx.cwd)
        diff = make_diff(before, after, rel)
        replacements = count if replace_all else 1
        return ToolResult.success(
            f"Edited {rel} ({replacements} replacement{'s' if replacements != 1 else ''})\n"
            f"{truncate_middle(diff, 6000, 'diff truncated')}",
            data={"path": str(target), "replacements": replacements, "diff": diff},
            metadata={"touched": [str(target)]},
        )


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete a file, or a directory when recursive=true. "
        "Check with list_directory first; this cannot be undone unless the file is tracked by git."
    )
    category = ToolCategory.FILESYSTEM
    mutating = True
    dangerous = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to remove."},
            "recursive": {"type": "boolean", "description": "Required to delete a non-empty directory.", "default": False},
        },
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, path: str, recursive: bool = False) -> ToolResult:
        target = ctx.resolve_path(path)
        if not target.exists() and not target.is_symlink():
            raise ToolError(f"path does not exist: {target}", FailureKind.NOT_FOUND)

        rel = shorten_path(target, ctx.cwd)
        if target.is_dir() and not target.is_symlink():
            entries = list(target.iterdir())
            if entries and not recursive:
                raise ToolError(
                    f"{rel} is a non-empty directory ({len(entries)} entries)",
                    FailureKind.INVALID_INPUT,
                    hint="Pass recursive=true if you really intend to delete the whole tree.",
                )
            shutil.rmtree(target)
            _record_change(ctx, target, "deleted")
            return ToolResult.success(
                f"Deleted directory {rel} ({len(entries)} entries)",
                data={"path": str(target), "kind": "directory", "entries": len(entries)},
                metadata={"touched": [str(target)]},
            )

        size = target.stat().st_size if target.exists() else 0
        target.unlink()
        _record_change(ctx, target, "deleted")
        return ToolResult.success(
            f"Deleted {rel} ({size} bytes)",
            data={"path": str(target), "kind": "file", "size": size},
            metadata={"touched": [str(target)]},
        )


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "List directory entries with type and size. Skips noisy directories "
        "(.git, node_modules, __pycache__, ...) unless show_all=true. "
        "Use depth>1 for a shallow tree view."
    )
    category = ToolCategory.FILESYSTEM
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list.", "default": "."},
            "depth": {"type": "integer", "description": "Recursion depth (1 = just this directory).", "default": 1},
            "show_all": {"type": "boolean", "description": "Include hidden and noisy entries.", "default": False},
            "pattern": {"type": "string", "description": "Optional glob filter, e.g. '*.py'."},
            "limit": {"type": "integer", "description": "Maximum entries to return.", "default": 400},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        path: str = ".",
        depth: int = 1,
        show_all: bool = False,
        pattern: str | None = None,
        limit: int = 400,
    ) -> ToolResult:
        root = ctx.resolve_path(path)
        if not root.exists():
            raise ToolError(f"directory does not exist: {root}", FailureKind.NOT_FOUND)
        if not root.is_dir():
            raise ToolError(
                f"{root} is not a directory",
                FailureKind.INVALID_INPUT,
                hint="Use read_file for files.",
            )

        depth = max(1, min(int(depth), 8))
        entries: list[str] = []
        truncated = False
        counts = {"files": 0, "directories": 0}

        def walk(directory: Path, level: int, prefix: str) -> None:
            nonlocal truncated
            if level > depth or truncated:
                return
            try:
                children = sorted(
                    directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                )
            except PermissionError:
                entries.append(f"{prefix}(permission denied)")
                return
            for child in children:
                if len(entries) >= limit:
                    truncated = True
                    return
                if not show_all:
                    if child.name in NOISE_DIRS:
                        continue
                    if child.name.startswith(".") and child.name not in {".lema", ".github", ".env.example"}:
                        continue
                if child.is_dir():
                    counts["directories"] += 1
                    entries.append(f"{prefix}{child.name}/")
                    if level < depth:
                        walk(child, level + 1, prefix + "  ")
                else:
                    if pattern and not fnmatch.fnmatch(child.name, pattern):
                        continue
                    counts["files"] += 1
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = 0
                    entries.append(f"{prefix}{child.name}  ({_human_size(size)})")

        walk(root, 1, "")
        rel = shorten_path(root, ctx.cwd)
        header = f"{rel}/  -  {counts['directories']} directories, {counts['files']} files"
        body = "\n".join(entries) if entries else "(empty)"
        if truncated:
            body += f"\n... truncated at {limit} entries"
        return ToolResult.success(
            f"{header}\n{body}",
            data={"path": str(root), **counts, "truncated": truncated},
        )


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = "Create a directory, including any missing parents."
    category = ToolCategory.FILESYSTEM
    mutating = True
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory to create."}},
        "required": ["path"],
    }

    async def run(self, ctx: ToolContext, path: str) -> ToolResult:
        target = ctx.resolve_path(path)
        if target.exists():
            if target.is_dir():
                return ToolResult.success(
                    f"{shorten_path(target, ctx.cwd)}/ already exists",
                    data={"path": str(target), "created": False},
                )
            raise ToolError(
                f"{target} exists and is a file",
                FailureKind.INVALID_INPUT,
            )
        target.mkdir(parents=True, exist_ok=True)
        _record_change(ctx, target, "created")
        return ToolResult.success(
            f"Created {shorten_path(target, ctx.cwd)}/",
            data={"path": str(target), "created": True},
        )


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename a file or directory."
    category = ToolCategory.FILESYSTEM
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing path."},
            "destination": {"type": "string", "description": "New path."},
            "overwrite": {"type": "boolean", "description": "Replace the destination if it exists.", "default": False},
        },
        "required": ["source", "destination"],
    }

    async def run(
        self, ctx: ToolContext, source: str, destination: str, overwrite: bool = False
    ) -> ToolResult:
        src = ctx.resolve_path(source, must_exist=True)
        dst = ctx.resolve_path(destination)
        if dst.exists():
            if not overwrite:
                raise ToolError(
                    f"destination already exists: {dst}",
                    FailureKind.INVALID_INPUT,
                    hint="Pass overwrite=true to replace it.",
                )
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _record_change(ctx, src, "deleted")
        _record_change(ctx, dst, "created")
        return ToolResult.success(
            f"Moved {shorten_path(src, ctx.cwd)} -> {shorten_path(dst, ctx.cwd)}",
            data={"source": str(src), "destination": str(dst)},
            metadata={"touched": [str(src), str(dst)]},
        )


class CopyFileTool(Tool):
    name = "copy_file"
    description = "Copy a file, or a directory tree when the source is a directory."
    category = ToolCategory.FILESYSTEM
    mutating = True
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Existing path."},
            "destination": {"type": "string", "description": "Destination path."},
            "overwrite": {"type": "boolean", "description": "Replace the destination if it exists.", "default": False},
        },
        "required": ["source", "destination"],
    }

    async def run(
        self, ctx: ToolContext, source: str, destination: str, overwrite: bool = False
    ) -> ToolResult:
        src = ctx.resolve_path(source, must_exist=True)
        dst = ctx.resolve_path(destination)
        if dst.exists() and not overwrite:
            raise ToolError(
                f"destination already exists: {dst}",
                FailureKind.INVALID_INPUT,
                hint="Pass overwrite=true to replace it.",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            kind = "directory"
        else:
            shutil.copy2(src, dst)
            kind = "file"
        _record_change(ctx, dst, "created")
        return ToolResult.success(
            f"Copied {kind} {shorten_path(src, ctx.cwd)} -> {shorten_path(dst, ctx.cwd)}",
            data={"source": str(src), "destination": str(dst), "kind": kind},
            metadata={"touched": [str(dst)]},
        )


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def build_filesystem_tools() -> list[Tool]:
    return [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        DeleteFileTool(),
        ListDirectoryTool(),
        CreateDirectoryTool(),
        MoveFileTool(),
        CopyFileTool(),
    ]
