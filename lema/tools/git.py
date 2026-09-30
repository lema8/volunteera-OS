"""Git tools.

These exist so the model does not have to hand-assemble plumbing commands for
the operations it performs constantly.  Anything not covered here is still
reachable through ``run_command`` - these tools are a convenience layer, not
a cage.

Policy: the agent never commits unless it is explicitly told to. ``git_commit``
exists, but it is a mutating tool and it refuses to run with an empty staging
area unless ``add_all`` is requested.
"""

from __future__ import annotations

import shlex
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
from lema.tools.shell import run_shell
from lema.util.tokens import truncate_middle


async def git(
    args: list[str], cwd: Path, *, timeout: float = 60.0, shell: str | None = None
) -> dict[str, Any]:
    command = "git " + " ".join(shlex.quote(a) for a in args)
    return await run_shell(command, cwd=cwd, timeout=timeout, shell=shell)


async def is_git_repo(cwd: Path, shell: str | None = None) -> bool:
    result = await git(["rev-parse", "--is-inside-work-tree"], cwd, timeout=10, shell=shell)
    return result["exit_code"] == 0 and result["stdout"].strip() == "true"


async def repo_summary(cwd: Path, shell: str | None = None) -> dict[str, Any]:
    """Compact repository state, used by the context builder."""
    if not await is_git_repo(cwd, shell):
        return {"is_repo": False}

    branch = await git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout=10, shell=shell)
    status = await git(["status", "--porcelain=v1", "--branch"], cwd, timeout=20, shell=shell)
    last = await git(
        ["log", "-1", "--pretty=format:%h %s (%cr)"], cwd, timeout=10, shell=shell
    )

    changed: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in status["stdout"].splitlines():
        if line.startswith("##") or not line.strip():
            continue
        code, _, name = line.partition(" ")
        code = line[:2]
        name = line[3:].strip()
        if code == "??":
            untracked.append(name)
        else:
            if code[0] not in (" ", "?"):
                staged.append(name)
            if code[1] not in (" ", "?"):
                changed.append(name)

    return {
        "is_repo": True,
        "branch": branch["stdout"].strip(),
        "staged": staged,
        "modified": changed,
        "untracked": untracked,
        "clean": not (staged or changed or untracked),
        "last_commit": last["stdout"].strip(),
    }


class _GitTool(Tool):
    category = ToolCategory.GIT

    async def _git(self, ctx: ToolContext, args: list[str], timeout: float = 60.0) -> dict[str, Any]:
        result = await git(args, ctx.cwd, timeout=timeout, shell=ctx.config.tools.shell)
        if result["exit_code"] == 128 and "not a git repository" in result["stderr"].lower():
            raise self._not_a_repo(ctx)
        return result

    @staticmethod
    def _not_a_repo(ctx: ToolContext) -> ToolError:
        return ToolError(
            f"{ctx.cwd} is not a git repository",
            FailureKind.CONFIGURATION,
            hint="Run `git init` first, or change to a directory inside a repository.",
        )

    async def _require_repo(self, ctx: ToolContext) -> None:
        """Fail with an accurate diagnosis before running a subcommand.

        Outside a repository some subcommands report a confusing secondary
        error instead (``git diff --cached`` says "unknown option `cached'"),
        so the check has to happen first.
        """
        probe = await git(
            ["rev-parse", "--git-dir"], ctx.cwd, timeout=15, shell=ctx.config.tools.shell
        )
        if probe["exit_code"] != 0:
            raise self._not_a_repo(ctx)


class GitStatusTool(_GitTool):
    name = "git_status"
    description = (
        "Show the working tree status: current branch, staged, modified and untracked files. "
        "Use this before any destructive operation to understand what would be lost."
    )
    read_only = True
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext) -> ToolResult:
        # Deliberately no _require_repo: "this is not a repo" is a useful
        # answer to `git_status`, not an error to recover from.
        summary = await repo_summary(ctx.cwd, ctx.config.tools.shell)
        if not summary["is_repo"]:
            return ToolResult.success(
                f"{ctx.cwd} is not a git repository.", data={"is_repo": False}
            )
        lines = [f"branch: {summary['branch']}", f"last commit: {summary['last_commit'] or '(none)'}"]
        if summary["clean"]:
            lines.append("working tree clean")
        else:
            if summary["staged"]:
                lines.append(f"staged ({len(summary['staged'])}):")
                lines += [f"  {f}" for f in summary["staged"][:50]]
            if summary["modified"]:
                lines.append(f"modified ({len(summary['modified'])}):")
                lines += [f"  {f}" for f in summary["modified"][:50]]
            if summary["untracked"]:
                lines.append(f"untracked ({len(summary['untracked'])}):")
                lines += [f"  {f}" for f in summary["untracked"][:50]]
        return ToolResult.success("\n".join(lines), data=summary)


class GitDiffTool(_GitTool):
    name = "git_diff"
    description = (
        "Show changes as a unified diff. Defaults to unstaged changes; "
        "set staged=true for the index, or pass `ref` to diff against a commit/branch."
    )
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Limit the diff to this path."},
            "staged": {"type": "boolean", "description": "Diff the staging area instead.", "default": False},
            "ref": {"type": "string", "description": "Diff against this commit/branch (e.g. 'HEAD~1', 'main')."},
            "stat_only": {"type": "boolean", "description": "Only show the summary of changed files.", "default": False},
            "max_chars": {"type": "integer", "description": "Truncate the diff to this size.", "default": 20000},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        path: str | None = None,
        staged: bool = False,
        ref: str | None = None,
        stat_only: bool = False,
        max_chars: int = 20000,
    ) -> ToolResult:
        await self._require_repo(ctx)
        args = ["diff"]
        if staged:
            args.append("--cached")
        if ref:
            args.append(ref)
        if stat_only:
            args.append("--stat")
        if path:
            args += ["--", path]
        result = await self._git(ctx, args)
        if result["exit_code"] != 0:
            return ToolResult.failure(
                f"git diff failed: {result['stderr'].strip()}",
                FailureKind.UNKNOWN,
                output=result["stdout"],
            )
        diff = result["stdout"]
        if not diff.strip():
            return ToolResult.success("No changes.", data={"empty": True})
        return ToolResult.success(
            truncate_middle(diff, int(max_chars), "diff truncated"),
            data={"empty": False, "chars": len(diff)},
        )


class GitLogTool(_GitTool):
    name = "git_log"
    description = "Show recent commits (hash, author, relative date, subject)."
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of commits.", "default": 15},
            "path": {"type": "string", "description": "Only commits touching this path."},
            "oneline": {"type": "boolean", "default": True},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        limit: int = 15,
        path: str | None = None,
        oneline: bool = True,
    ) -> ToolResult:
        await self._require_repo(ctx)
        fmt = "%h %an %cr: %s" if oneline else "%H%n%an <%ae>%n%cd%n%n%B%n---"
        args = ["log", f"-{max(1, int(limit))}", f"--pretty=format:{fmt}"]
        if path:
            args += ["--", path]
        result = await self._git(ctx, args)
        if result["exit_code"] != 0:
            stderr = result["stderr"].strip()
            if "does not have any commits" in stderr:
                return ToolResult.success("Repository has no commits yet.", data={"commits": 0})
            return ToolResult.failure(f"git log failed: {stderr}", FailureKind.UNKNOWN)
        out = result["stdout"].strip() or "(no commits)"
        return ToolResult.success(out, data={"commits": len(out.splitlines())})


class GitBranchTool(_GitTool):
    name = "git_branch"
    description = (
        "List branches, or create/switch to one. "
        "With no arguments it lists local branches and marks the current one."
    )
    mutating = True
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Branch to create or switch to."},
            "create": {"type": "boolean", "description": "Create the branch if it does not exist.", "default": False},
            "list_all": {"type": "boolean", "description": "Include remote-tracking branches.", "default": False},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        name: str | None = None,
        create: bool = False,
        list_all: bool = False,
    ) -> ToolResult:
        await self._require_repo(ctx)
        if not name:
            args = ["branch", "-v"]
            if list_all:
                args.append("-a")
            result = await self._git(ctx, args)
            return ToolResult.success(
                result["stdout"].strip() or "(no branches)", data={"raw": result["stdout"]}
            )

        args = ["switch"]
        if create:
            args.append("-c")
        args.append(name)
        result = await self._git(ctx, args)
        if result["exit_code"] != 0:
            stderr = result["stderr"].strip()
            hint = None
            if "already exists" in stderr:
                hint = "The branch exists; call again with create=false to switch to it."
            elif "invalid reference" in stderr or "did not match" in stderr:
                hint = "The branch does not exist; call again with create=true."
            return ToolResult.failure(
                f"git switch failed: {stderr}", FailureKind.CONFIGURATION, hint=hint
            )
        return ToolResult.success(
            (result["stdout"] + result["stderr"]).strip() or f"Switched to {name}",
            data={"branch": name, "created": create},
        )


class GitCommitTool(_GitTool):
    name = "git_commit"
    description = (
        "Create a commit. Only use this when the user explicitly asks for a commit. "
        "Set add_all=true to stage all modified and untracked files first."
    )
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message."},
            "add_all": {"type": "boolean", "description": "Stage all changes before committing.", "default": False},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific paths to stage before committing.",
            },
        },
        "required": ["message"],
    }

    async def run(
        self,
        ctx: ToolContext,
        message: str,
        add_all: bool = False,
        paths: list[str] | None = None,
    ) -> ToolResult:
        await self._require_repo(ctx)
        if not message.strip():
            raise ToolError("commit message is empty", FailureKind.INVALID_INPUT)

        if paths:
            add = await self._git(ctx, ["add", "--", *paths])
            if add["exit_code"] != 0:
                return ToolResult.failure(
                    f"git add failed: {add['stderr'].strip()}", FailureKind.UNKNOWN
                )
        elif add_all:
            add = await self._git(ctx, ["add", "-A"])
            if add["exit_code"] != 0:
                return ToolResult.failure(
                    f"git add failed: {add['stderr'].strip()}", FailureKind.UNKNOWN
                )

        staged = await self._git(ctx, ["diff", "--cached", "--name-only"])
        if not staged["stdout"].strip():
            return ToolResult.failure(
                "nothing staged to commit",
                FailureKind.INVALID_INPUT,
                hint="Pass add_all=true or list `paths` to stage changes first.",
            )

        result = await self._git(ctx, ["commit", "-m", message])
        if result["exit_code"] != 0:
            stderr = result["stderr"].strip()
            hint = None
            if "user.email" in stderr or "user.name" in stderr:
                hint = "Git identity is not configured. Set user.name and user.email."
            return ToolResult.failure(
                f"git commit failed: {stderr}", FailureKind.CONFIGURATION, hint=hint
            )
        return ToolResult.success(
            result["stdout"].strip(),
            data={"files": staged["stdout"].split(), "message": message},
        )


class GitShowTool(_GitTool):
    name = "git_show"
    description = "Show a commit's metadata and diff, or a file's contents at a given revision."
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "Commit-ish, e.g. 'HEAD', 'abc123', 'main~2'.", "default": "HEAD"},
            "path": {"type": "string", "description": "Optional file path to show at that revision."},
            "max_chars": {"type": "integer", "default": 20000},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        ref: str = "HEAD",
        path: str | None = None,
        max_chars: int = 20000,
    ) -> ToolResult:
        await self._require_repo(ctx)
        target = f"{ref}:{path}" if path else ref
        result = await self._git(ctx, ["show", target])
        if result["exit_code"] != 0:
            return ToolResult.failure(
                f"git show failed: {result['stderr'].strip()}",
                FailureKind.NOT_FOUND,
                hint="Check the ref and path with git_log.",
            )
        return ToolResult.success(
            truncate_middle(result["stdout"], int(max_chars), "output truncated"),
            data={"ref": target},
        )


def build_git_tools() -> list[Tool]:
    return [
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
        GitBranchTool(),
        GitCommitTool(),
        GitShowTool(),
    ]
