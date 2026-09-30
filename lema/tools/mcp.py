"""Model Context Protocol (MCP) client - stdio transport.

MCP is optional: nothing in the harness requires it.  But because the tool
registry is provider-neutral, wiring an external MCP server in is just a
matter of translating its tool list into :class:`Tool` objects, which is what
this module does.

This is a genuine JSON-RPC 2.0 client over the server's stdin/stdout, not a
placeholder.  It implements the subset Lema needs: ``initialize``,
``tools/list`` and ``tools/call``.

Configure servers in config.toml::

    [tools.mcp_servers.filesystem]
    command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Sequence

from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolResult,
)
from lema.tools.registry import ToolRegistry

PROTOCOL_VERSION = "2024-11-05"


class MCPError(Exception):
    pass


@dataclass
class MCPServer:
    """A running MCP server subprocess speaking JSON-RPC over stdio."""

    name: str
    command: Sequence[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    process: asyncio.subprocess.Process | None = None
    _next_id: int = 1
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False
    server_info: dict[str, Any] = field(default_factory=dict)

    async def start(self, timeout: float = 30.0) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        env = {**os.environ, **self.env}
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            raise MCPError(f"MCP server {self.name!r}: command not found: {self.command[0]}") from exc

        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "lema-harness", "version": "0.1.0"},
            },
            timeout=timeout,
        )
        self.server_info = result.get("serverInfo", {})
        await self._notify("notifications/initialized", {})
        self.initialized = True

    async def stop(self) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
        self.process = None
        self.initialized = False

    async def _write(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise MCPError(f"MCP server {self.name!r} is not running")
        data = (json.dumps(payload) + "\n").encode()
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float = 60.0
    ) -> dict[str, Any]:
        async with self._lock:
            if self.process is None or self.process.stdout is None:
                raise MCPError(f"MCP server {self.name!r} is not running")
            request_id = self._next_id
            self._next_id += 1
            await self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )

            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise MCPError(f"MCP server {self.name!r}: {method} timed out")
                try:
                    line = await asyncio.wait_for(
                        self.process.stdout.readline(), timeout=remaining
                    )
                except asyncio.TimeoutError as exc:
                    raise MCPError(f"MCP server {self.name!r}: {method} timed out") from exc
                if not line:
                    stderr = b""
                    if self.process.stderr is not None:
                        try:
                            stderr = await asyncio.wait_for(self.process.stderr.read(2000), 1)
                        except asyncio.TimeoutError:
                            pass
                    raise MCPError(
                        f"MCP server {self.name!r} closed the connection. "
                        f"stderr: {stderr.decode('utf-8', 'replace')[:500]}"
                    )
                try:
                    message = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue  # servers sometimes emit log lines on stdout
                if message.get("id") != request_id:
                    continue  # a notification or a response to another call
                if "error" in message:
                    error = message["error"]
                    raise MCPError(
                        f"MCP server {self.name!r}: {error.get('message')} "
                        f"(code {error.get('code')})"
                    )
                return message.get("result") or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        return list(result.get("tools") or [])

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
        return await self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )


class MCPTool(Tool):
    """Adapter exposing one MCP server tool through Lema's registry."""

    category = ToolCategory.OTHER
    mutating = True   # unknown semantics; treat as mutating for permissions
    read_only = False

    def __init__(self, server: MCPServer, definition: dict[str, Any], prefix: bool = True):
        raw_name = str(definition.get("name") or "")
        self.name = f"{server.name}_{raw_name}" if prefix else raw_name
        self.remote_name = raw_name
        self.description = str(
            definition.get("description") or f"MCP tool {raw_name} from server {server.name}"
        )
        schema = definition.get("inputSchema") or definition.get("input_schema") or {}
        self.parameters = schema if isinstance(schema, dict) and schema else {
            "type": "object",
            "properties": {},
        }
        self.server = server
        super().__init__()

    async def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        try:
            result = await self.server.call_tool(self.remote_name, kwargs)
        except MCPError as exc:
            return ToolResult.failure(
                str(exc),
                FailureKind.CONFIGURATION,
                hint="The MCP server may have crashed; check its configuration.",
            )

        parts: list[str] = []
        for block in result.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "resource":
                resource = block.get("resource") or {}
                parts.append(str(resource.get("text") or resource.get("uri") or ""))
            else:
                parts.append(json.dumps(block)[:2000])
        body = "\n".join(p for p in parts if p) or json.dumps(result, default=str)[:4000]

        if result.get("isError"):
            return ToolResult.failure(body, FailureKind.UNKNOWN, output=body)
        return ToolResult.success(body, data={"mcp_result": result})


class MCPManager:
    """Owns MCP server subprocesses for a session."""

    def __init__(self) -> None:
        self.servers: dict[str, MCPServer] = {}

    async def connect_all(
        self, definitions: dict[str, Any], registry: ToolRegistry, *, cwd: str | None = None
    ) -> tuple[list[str], list[str]]:
        """Start each configured server and register its tools.

        Returns ``(registered_tool_names, errors)``.
        """
        registered: list[str] = []
        errors: list[str] = []

        for name, spec in (definitions or {}).items():
            if not isinstance(spec, dict):
                errors.append(f"mcp server {name!r}: expected a table")
                continue
            if spec.get("enabled") is False:
                continue
            command = spec.get("command")
            if isinstance(command, str):
                command = command.split()
            if not command:
                errors.append(f"mcp server {name!r}: no command configured")
                continue

            server = MCPServer(
                name=name,
                command=list(command),
                env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                cwd=spec.get("cwd") or cwd,
            )
            try:
                await server.start(timeout=float(spec.get("timeout") or 30.0))
                definitions_list = await server.list_tools()
            except (MCPError, OSError) as exc:
                errors.append(f"mcp server {name!r}: {exc}")
                await server.stop()
                continue

            self.servers[name] = server
            for definition in definitions_list:
                try:
                    mcp_tool = MCPTool(server, definition)
                    registry.register(mcp_tool, replace=True)
                    registered.append(mcp_tool.name)
                except (ValueError, TypeError) as exc:
                    errors.append(f"mcp server {name!r}: bad tool definition: {exc}")

        return registered, errors

    async def shutdown(self) -> None:
        for server in list(self.servers.values()):
            try:
                await server.stop()
            except Exception:  # noqa: BLE001
                continue
        self.servers.clear()
