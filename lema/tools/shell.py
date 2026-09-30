"""Shell execution.

Linux-first: commands run through the configured shell (``/bin/bash`` by
default) in a new process *group*, so a timeout kills the whole tree rather
than orphaning children - the classic failure mode when an agent runs
``npm install`` or a compiler that spawns workers.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import time
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
from lema.tools.classify import classify_output
from lema.util.paths import shorten_path
from lema.util.tokens import truncate_middle


def pick_shell(configured: str | None = None) -> str:
    """Choose an available shell, preferring the configured one."""
    candidates = [configured, os.environ.get("SHELL"), "/bin/bash", "/usr/bin/bash", "/bin/sh"]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "/bin/sh"


async def run_shell(
    command: str,
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    shell: str | None = None,
    stdin: str | None = None,
    max_output_chars: int = 100_000,
) -> dict[str, Any]:
    """Run a command and capture its output.

    Returns a dict with command, working_directory, exit_code, stdout,
    stderr, duration_ms and timed_out.
    """
    shell_bin = pick_shell(shell)
    process_env = {**os.environ, **(env or {})}
    # Make tools behave predictably and non-interactively.
    process_env.setdefault("LEMA_AGENT", "1")
    process_env.setdefault("CI", "1")
    process_env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    process_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    process_env.setdefault("GIT_TERMINAL_PROMPT", "0")

    start = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            shell_bin,
            "-c",
            command,
            cwd=str(cwd),
            env=process_env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group -> killable as a tree
        )
    except FileNotFoundError as exc:
        raise ToolError(
            f"shell not available: {shell_bin} ({exc})",
            FailureKind.CONFIGURATION,
            hint="Set tools.shell in the config to a shell that exists.",
        ) from exc
    except NotADirectoryError as exc:
        raise ToolError(
            f"working directory is not a directory: {cwd}",
            FailureKind.NOT_FOUND,
        ) from exc

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(stdin.encode() if stdin is not None else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        timed_out = True
        _terminate_group(process)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            stdout_bytes, stderr_bytes = b"", b""
    except asyncio.CancelledError:
        _terminate_group(process)
        raise

    duration_ms = (time.perf_counter() - start) * 1000
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    return {
        "command": command,
        "working_directory": str(cwd),
        "exit_code": -1 if timed_out else (process.returncode if process.returncode is not None else -1),
        "stdout": truncate_middle(stdout, max_output_chars, "stdout truncated"),
        "stderr": truncate_middle(stderr, max_output_chars, "stderr truncated"),
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "shell": shell_bin,
    }


def _terminate_group(process: asyncio.subprocess.Process) -> None:
    """SIGTERM then SIGKILL the whole process group."""
    if process.returncode is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except (ProcessLookupError, PermissionError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        if sig is signal.SIGTERM:
            time.sleep(0.15)
            if process.returncode is not None:
                return


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command and capture stdout, stderr, exit code and duration. "
        "Runs in the session working directory unless `cwd` is given. "
        "Use this for builds, tests, package managers, linters, and any CLI. "
        "For long-running servers use start_process instead - this tool waits for completion."
    )
    category = ToolCategory.SHELL
    mutating = True
    dangerous = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command line to execute."},
            "cwd": {"type": "string", "description": "Working directory (default: session cwd)."},
            "timeout": {"type": "number", "description": "Seconds before the process tree is killed."},
            "env": {"type": "object", "description": "Extra environment variables for this command."},
            "stdin": {"type": "string", "description": "Text piped to the command's stdin."},
        },
        "required": ["command"],
    }

    def summarize_call(self, arguments: dict[str, Any]) -> str:
        command = str(arguments.get("command", "")).strip().replace("\n", " ⏎ ")
        if len(command) > 100:
            command = command[:97] + "..."
        return command or self.name

    async def run(
        self,
        ctx: ToolContext,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> ToolResult:
        if not command.strip():
            raise ToolError("command is empty", FailureKind.INVALID_INPUT)

        workdir = ctx.resolve_path(cwd) if cwd else ctx.cwd
        if not workdir.is_dir():
            raise ToolError(
                f"working directory does not exist: {workdir}",
                FailureKind.NOT_FOUND,
            )

        limit = float(timeout) if timeout else ctx.config.tools.shell_timeout
        result = await run_shell(
            command,
            cwd=workdir,
            env={str(k): str(v) for k, v in (env or {}).items()},
            timeout=limit,
            shell=ctx.config.tools.shell,
            stdin=stdin,
            max_output_chars=ctx.config.tools.shell_max_output_chars,
        )

        combined = f"{result['stdout']}\n{result['stderr']}".strip()
        kind, hint = classify_output(
            combined, result["exit_code"], timed_out=result["timed_out"]
        )

        body_parts = [
            f"$ {command}",
            f"cwd: {shorten_path(workdir, ctx.cwd)}",
            f"exit_code: {result['exit_code']}"
            + (" (TIMED OUT)" if result["timed_out"] else ""),
            f"duration: {result['duration_ms'] / 1000:.2f}s",
        ]
        if result["stdout"].strip():
            body_parts.append(f"--- stdout ---\n{result['stdout'].rstrip()}")
        if result["stderr"].strip():
            body_parts.append(f"--- stderr ---\n{result['stderr'].rstrip()}")
        if not result["stdout"].strip() and not result["stderr"].strip():
            body_parts.append("(no output)")
        body = "\n".join(body_parts)

        commands_run = ctx.scratch.setdefault("commands", [])
        commands_run.append({"command": command, "exit_code": result["exit_code"]})

        if result["timed_out"]:
            return ToolResult.failure(
                f"command timed out after {limit:.0f}s",
                FailureKind.TIMEOUT,
                hint=hint,
                output=body,
                data=result,
            )
        if result["exit_code"] != 0:
            return ToolResult.failure(
                f"command exited with code {result['exit_code']}",
                kind if kind is not FailureKind.NONE else FailureKind.UNKNOWN,
                hint=hint,
                output=body,
                data=result,
            )
        return ToolResult.success(body, data=result)


class WhichTool(Tool):
    name = "which"
    description = (
        "Check whether one or more executables are available on PATH, and report "
        "their version. Use this before assuming a toolchain exists."
    )
    category = ToolCategory.SHELL
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "programs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Executable names to look for.",
            }
        },
        "required": ["programs"],
    }

    async def run(self, ctx: ToolContext, programs: list[str]) -> ToolResult:
        import shutil as _shutil

        lines: list[str] = []
        found: dict[str, str | None] = {}
        for program in programs:
            path = _shutil.which(program)
            found[program] = path
            if path is None:
                lines.append(f"{program}: NOT FOUND")
                continue
            version = ""
            for flag in ("--version", "-version", "-V", "version"):
                probe = await run_shell(
                    f"{shlex.quote(path)} {flag}",
                    cwd=ctx.cwd,
                    timeout=10,
                    shell=ctx.config.tools.shell,
                )
                if probe["exit_code"] == 0 and (probe["stdout"].strip() or probe["stderr"].strip()):
                    version = (probe["stdout"] or probe["stderr"]).strip().splitlines()[0]
                    break
            lines.append(f"{program}: {path}" + (f"  ({version})" if version else ""))
        return ToolResult.success("\n".join(lines), data={"found": found})


def build_shell_tools() -> list[Tool]:
    return [RunCommandTool(), WhichTool()]
