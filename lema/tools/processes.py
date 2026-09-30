"""Background process management.

Long-running things (dev servers, watchers, databases) cannot be run through
``run_command`` because it blocks until exit.  The :class:`ProcessManager`
keeps them alive across tool calls, buffers their output, and guarantees they
are reaped when the session ends.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from collections import deque
from dataclasses import dataclass, field
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
from lema.tools.shell import pick_shell
from lema.util.paths import shorten_path

MAX_LOG_LINES = 2000


@dataclass
class ManagedProcess:
    id: str
    command: str
    cwd: str
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.time)
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    readers: list[asyncio.Task[None]] = field(default_factory=list)
    exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self.process.returncode is None

    @property
    def uptime(self) -> float:
        return time.time() - self.started_at

    def status(self) -> str:
        if self.running:
            return "running"
        return f"exited({self.process.returncode})"

    def tail(self, lines: int = 80) -> str:
        items = list(self.logs)[-lines:]
        return "\n".join(items) if items else "(no output yet)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.process.pid,
            "status": self.status(),
            "running": self.running,
            "uptime_s": round(self.uptime, 1),
            "exit_code": self.process.returncode,
            "log_lines": len(self.logs),
        }


class ProcessManager:
    """Owns every background process started during a session."""

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._counter = 0

    def _next_id(self, command: str) -> str:
        self._counter += 1
        base = re.split(r"[\s/]+", command.strip())[0] or "proc"
        base = re.sub(r"[^A-Za-z0-9_.-]", "", base)[:16] or "proc"
        return f"{base}-{self._counter}"

    async def start(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        shell: str | None = None,
        name: str | None = None,
    ) -> ManagedProcess:
        process_env = {**os.environ, **(env or {})}
        process_env.setdefault("LEMA_AGENT", "1")
        shell_bin = pick_shell(shell)

        process = await asyncio.create_subprocess_exec(
            shell_bin,
            "-c",
            command,
            cwd=str(cwd),
            env=process_env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        pid = name or self._next_id(command)
        if pid in self._processes:
            pid = f"{pid}-{self._counter}"
        managed = ManagedProcess(id=pid, command=command, cwd=str(cwd), process=process)
        self._processes[pid] = managed

        async def pump() -> None:
            assert process.stdout is not None
            try:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    managed.logs.append(line.decode("utf-8", errors="replace").rstrip("\n"))
            except (asyncio.CancelledError, ValueError):
                pass
            finally:
                managed.exit_code = process.returncode

        managed.readers.append(asyncio.create_task(pump()))
        return managed

    def get(self, process_id: str) -> ManagedProcess | None:
        return self._processes.get(process_id)

    def all(self) -> list[ManagedProcess]:
        return list(self._processes.values())

    async def stop(self, process_id: str, *, timeout: float = 5.0) -> ManagedProcess:
        managed = self._processes.get(process_id)
        if managed is None:
            raise KeyError(process_id)
        if not managed.running:
            return managed
        try:
            pgid = os.getpgid(managed.process.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                managed.process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(managed.process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(managed.process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    managed.process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(managed.process.wait(), timeout=timeout)
            except asyncio.TimeoutError:  # pragma: no cover
                pass
        for task in managed.readers:
            task.cancel()
        managed.exit_code = managed.process.returncode
        return managed

    async def wait_for_log(
        self, process_id: str, pattern: str, *, timeout: float = 30.0
    ) -> tuple[bool, str]:
        """Block until the process output matches ``pattern`` (regex)."""
        managed = self._processes.get(process_id)
        if managed is None:
            raise KeyError(process_id)
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            joined = "\n".join(managed.logs)
            if regex.search(joined):
                return True, joined
            if not managed.running:
                return False, joined
            await asyncio.sleep(0.2)
        return False, "\n".join(managed.logs)

    async def shutdown(self) -> None:
        """Stop everything. Called when the session ends."""
        for process_id in list(self._processes):
            try:
                await self.stop(process_id, timeout=3.0)
            except Exception:  # noqa: BLE001 - best effort cleanup
                continue


def _require_manager(ctx: ToolContext) -> ProcessManager:
    if ctx.processes is None:
        raise ToolError(
            "process management is not available in this context",
            FailureKind.CONFIGURATION,
        )
    return ctx.processes


class StartProcessTool(Tool):
    name = "start_process"
    description = (
        "Start a long-running background process (dev server, watcher, database) and return "
        "immediately with a process id. Use get_process_output to read its logs and "
        "stop_process to terminate it. For commands that finish on their own, use run_command."
    )
    category = ToolCategory.PROCESS
    mutating = True
    dangerous = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command line to run in the background."},
            "cwd": {"type": "string", "description": "Working directory (default: session cwd)."},
            "name": {"type": "string", "description": "Optional identifier for the process."},
            "env": {"type": "object", "description": "Extra environment variables."},
            "wait_for_log": {
                "type": "string",
                "description": "Optional regex; wait until the output matches it (e.g. 'listening on').",
            },
            "wait_timeout": {"type": "number", "description": "Seconds to wait for wait_for_log.", "default": 30},
        },
        "required": ["command"],
    }

    async def run(
        self,
        ctx: ToolContext,
        command: str,
        cwd: str | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
        wait_for_log: str | None = None,
        wait_timeout: float = 30,
    ) -> ToolResult:
        manager = _require_manager(ctx)
        workdir = ctx.resolve_path(cwd) if cwd else ctx.cwd
        if not workdir.is_dir():
            raise ToolError(f"working directory does not exist: {workdir}", FailureKind.NOT_FOUND)

        managed = await manager.start(
            command,
            cwd=workdir,
            env={str(k): str(v) for k, v in (env or {}).items()},
            shell=ctx.config.tools.shell,
            name=name,
        )
        # Give it a moment so instant failures are reported straight away.
        await asyncio.sleep(0.4)

        matched = None
        if wait_for_log:
            matched, _ = await manager.wait_for_log(
                managed.id, wait_for_log, timeout=float(wait_timeout)
            )

        info = managed.to_dict()
        lines = [
            f"Started process '{managed.id}' (pid {managed.process.pid})",
            f"command: {command}",
            f"cwd: {shorten_path(workdir, ctx.cwd)}",
            f"status: {managed.status()}",
        ]
        if wait_for_log:
            lines.append(
                f"wait_for_log {wait_for_log!r}: {'MATCHED' if matched else 'NOT matched within timeout'}"
            )
        lines.append("--- recent output ---")
        lines.append(managed.tail(40))

        if not managed.running and (managed.process.returncode or 0) != 0:
            return ToolResult.failure(
                f"process exited immediately with code {managed.process.returncode}",
                FailureKind.CONFIGURATION,
                hint="Read the output above; the command failed at startup.",
                output="\n".join(lines),
                data=info,
            )
        return ToolResult.success("\n".join(lines), data=info)


class StopProcessTool(Tool):
    name = "stop_process"
    description = "Stop a background process by id (SIGTERM, then SIGKILL). Returns its final output."
    category = ToolCategory.PROCESS
    mutating = True
    parameters = {
        "type": "object",
        "properties": {
            "process_id": {"type": "string", "description": "Id returned by start_process."},
            "timeout": {"type": "number", "description": "Grace period before SIGKILL.", "default": 5},
        },
        "required": ["process_id"],
    }

    async def run(self, ctx: ToolContext, process_id: str, timeout: float = 5) -> ToolResult:
        manager = _require_manager(ctx)
        try:
            managed = await manager.stop(process_id, timeout=float(timeout))
        except KeyError:
            known = ", ".join(p.id for p in manager.all()) or "(none)"
            raise ToolError(
                f"no such process: {process_id}",
                FailureKind.NOT_FOUND,
                hint=f"Known processes: {known}",
            ) from None
        return ToolResult.success(
            f"Stopped '{managed.id}' (exit code {managed.process.returncode})\n"
            f"--- final output ---\n{managed.tail(40)}",
            data=managed.to_dict(),
        )


class ListProcessesTool(Tool):
    name = "list_processes"
    description = "List background processes started in this session with their status and uptime."
    category = ToolCategory.PROCESS
    read_only = True
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext) -> ToolResult:
        manager = _require_manager(ctx)
        processes = manager.all()
        if not processes:
            return ToolResult.success("No background processes.", data={"processes": []})
        lines = [f"{'ID':<18} {'STATUS':<14} {'UPTIME':<10} COMMAND"]
        for managed in processes:
            lines.append(
                f"{managed.id:<18} {managed.status():<14} {managed.uptime:>7.1f}s  {managed.command[:60]}"
            )
        return ToolResult.success(
            "\n".join(lines), data={"processes": [p.to_dict() for p in processes]}
        )


class GetProcessOutputTool(Tool):
    name = "get_process_output"
    description = (
        "Read buffered output from a background process. "
        "Optionally block until the output matches a regex (useful for 'server started' messages)."
    )
    category = ToolCategory.PROCESS
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "process_id": {"type": "string", "description": "Id returned by start_process."},
            "lines": {"type": "integer", "description": "How many trailing lines to return.", "default": 80},
            "wait_for": {"type": "string", "description": "Optional regex to wait for."},
            "timeout": {"type": "number", "description": "Seconds to wait when wait_for is set.", "default": 30},
        },
        "required": ["process_id"],
    }

    async def run(
        self,
        ctx: ToolContext,
        process_id: str,
        lines: int = 80,
        wait_for: str | None = None,
        timeout: float = 30,
    ) -> ToolResult:
        manager = _require_manager(ctx)
        managed = manager.get(process_id)
        if managed is None:
            known = ", ".join(p.id for p in manager.all()) or "(none)"
            raise ToolError(
                f"no such process: {process_id}",
                FailureKind.NOT_FOUND,
                hint=f"Known processes: {known}",
            )
        matched = None
        if wait_for:
            matched, _ = await manager.wait_for_log(process_id, wait_for, timeout=float(timeout))

        header = f"process '{managed.id}' status={managed.status()} uptime={managed.uptime:.1f}s"
        if wait_for:
            header += f"\nwait_for {wait_for!r}: {'MATCHED' if matched else 'not matched'}"
        body = f"{header}\n--- output (last {lines} lines) ---\n{managed.tail(int(lines))}"

        data = {**managed.to_dict(), "matched": matched}
        if wait_for and not matched:
            return ToolResult.failure(
                f"pattern {wait_for!r} did not appear within {timeout}s",
                FailureKind.TIMEOUT,
                hint="The process may have failed to start; inspect the output above.",
                output=body,
                data=data,
            )
        return ToolResult.success(body, data=data)


def build_process_tools() -> list[Tool]:
    return [
        StartProcessTool(),
        StopProcessTool(),
        ListProcessesTool(),
        GetProcessOutputTool(),
    ]
