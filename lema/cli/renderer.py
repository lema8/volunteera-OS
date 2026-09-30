"""Terminal rendering.

Consumes :class:`AgentEvent` and produces the terminal output.  Kept separate
from the agent so the runtime stays headless.

Output philosophy: show what the agent is doing and what came back, but do not
drown the user in raw tool output.  Full detail lives in the session log.
"""

from __future__ import annotations

import os

import shutil
import sys
import time
from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from lema.agent.events import AgentEvent, EventType
from lema.config.schema import Config, PermissionMode
from lema.tools.base import FailureKind, ToolResult
from lema.util.paths import display_path, shorten_path

#: Terse colour scheme; readable on both light and dark terminals.
STYLE_TOOL = "cyan"
STYLE_OK = "green"
STYLE_FAIL = "red"
STYLE_DIM = "grey50"
STYLE_WARN = "yellow"
STYLE_SKILL = "magenta"
STYLE_ACCENT = "bold cyan"

FAILURE_ICONS = {
    FailureKind.DEPENDENCY: "pkg",
    FailureKind.SYNTAX: "syn",
    FailureKind.PERMISSION: "perm",
    FailureKind.NOT_FOUND: "404",
    FailureKind.TIMEOUT: "time",
    FailureKind.TEST_FAILURE: "test",
    FailureKind.CONFIGURATION: "cfg",
    FailureKind.INVALID_INPUT: "args",
    FailureKind.DENIED: "deny",
    FailureKind.TEMPORARY: "temp",
}


class Renderer:
    """Renders agent events to a rich console."""

    def __init__(self, config: Config, console: Console | None = None):
        self.config = config
        self.console = console or Console(
            highlight=False,
            soft_wrap=False,
            no_color=not config.ui.color or bool(os.environ.get("NO_COLOR")),
        )
        self._streaming = False
        self._stream_chars = 0
        self._tool_started: dict[str, float] = {}
        self._active_tools: dict[str, str] = {}
        self._turn_started = 0.0
        #: Text already shown for this turn, so the end-of-turn summary does
        #: not reprint the answer the model already gave us.
        self._shown_text = ""

    # ------------------------------------------------------------- chrome

    def banner(self, *, model: str, provider: str, cwd: str, skills: int, tools: int) -> None:
        if not self.config.ui.banner:
            return
        width = min(shutil.get_terminal_size((80, 24)).columns - 2, 62)
        title = Text("LEMA HARNESS", style="bold white")
        subtitle = Text(f"{provider} · {model}", style=STYLE_ACCENT)
        details = Text(
            f"{tools} tools · {skills} skills · {self.config.permissions.value} mode",
            style=STYLE_DIM,
        )
        body = Group(
            Text(""),
            Text(title.plain, style="bold", justify="center"),
            Text(subtitle.plain, style=STYLE_ACCENT, justify="center"),
            Text(details.plain, style=STYLE_DIM, justify="center"),
            Text(""),
        )
        self.console.print(Panel(body, width=width, border_style=STYLE_TOOL, padding=(0, 1)))
        self.console.print(Text(display_path(cwd), style=STYLE_DIM))
        if self.config.permissions is PermissionMode.UNRESTRICTED:
            self.console.print(
                Text(
                    "unrestricted mode - commands run without confirmation",
                    style=STYLE_WARN,
                )
            )
        self.console.print()

    def warning(self, text: str) -> None:
        self.console.print(Text(f"! {text}", style=STYLE_WARN))

    def error(self, text: str) -> None:
        self.console.print(Text(f"✗ {text}", style=STYLE_FAIL))

    def info(self, text: str) -> None:
        self.console.print(Text(text, style=STYLE_DIM))

    def success(self, text: str) -> None:
        self.console.print(Text(f"✓ {text}", style=STYLE_OK))

    def rule(self, text: str = "") -> None:
        self.console.print(Rule(text, style=STYLE_DIM))

    def print_markdown(self, text: str) -> None:
        if not text.strip():
            return
        try:
            self.console.print(Markdown(text))
        except Exception:  # noqa: BLE001 - never let formatting break output
            self.console.print(text)

    # ------------------------------------------------------------- events

    async def handle(self, event: AgentEvent) -> None:
        handler = getattr(self, f"_on_{event.type.value}", None)
        if handler is not None:
            handler(event)

    # Each handler is small and named after the event for easy extension.

    def _on_turn_start(self, event: AgentEvent) -> None:
        self._turn_started = time.perf_counter()
        self._streaming = False
        self._stream_chars = 0
        self._shown_text = ""

    def _on_text_delta(self, event: AgentEvent) -> None:
        if not self.config.ui.stream_output:
            return
        if not self._streaming:
            self._streaming = True
            self._stream_chars = 0
        sys.stdout.write(event.text)
        sys.stdout.flush()
        self._stream_chars += len(event.text)

    def _end_stream(self) -> None:
        if self._streaming:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._streaming = False

    def _on_assistant_message(self, event: AgentEvent) -> None:
        if self._streaming:
            self._end_stream()
            return
        text = event.text.strip()
        if text:
            self._shown_text = text
            self.print_markdown(text)

    def _on_tool_start(self, event: AgentEvent) -> None:
        self._end_stream()
        self._tool_started[event.tool_call_id] = time.perf_counter()
        summary = self._summarize(event.tool_name, event.arguments)
        self._active_tools[event.tool_call_id] = summary

        line = Text("● ", style=STYLE_TOOL)
        line.append(event.tool_name, style=STYLE_TOOL)
        if self.config.ui.show_tool_args and summary:
            line.append("  ", style="")
            line.append(summary, style=STYLE_DIM)
        self.console.print(line)

    def _on_tool_end(self, event: AgentEvent) -> None:
        result = event.result
        if result is None:
            return
        elapsed = result.duration_ms / 1000

        if result.ok:
            detail = self._result_detail(event.tool_name, result)
            line = Text("  └─ ", style=STYLE_DIM)
            line.append("ok", style=STYLE_OK)
            if detail:
                line.append(f"  {detail}", style=STYLE_DIM)
            if self.config.ui.show_timings and elapsed >= 0.5:
                line.append(f"  ({elapsed:.1f}s)", style=STYLE_DIM)
            self.console.print(line)
        else:
            tag = FAILURE_ICONS.get(result.failure_kind, result.failure_kind.value)
            line = Text("  └─ ", style=STYLE_DIM)
            line.append(f"{tag}", style=STYLE_FAIL)
            line.append(f"  {self._first_line(result.error or '')}", style=STYLE_FAIL)
            self.console.print(line)
            preview = self._failure_preview(result)
            for text in preview:
                self.console.print(Text(f"     {text}", style=STYLE_DIM))

    def _on_skill_event(self, event: AgentEvent) -> None:
        self._end_stream()
        self.console.print(Text(f"  ◆ {event.text}", style=STYLE_SKILL))

    def _on_compaction(self, event: AgentEvent) -> None:
        self._end_stream()
        self.console.print(Text(f"  ⟳ {event.text}", style=STYLE_DIM))

    def _on_warning(self, event: AgentEvent) -> None:
        self._end_stream()
        self.console.print(Text(f"  ! {self._first_line(event.text)}", style=STYLE_WARN))

    def _on_retry(self, event: AgentEvent) -> None:
        self._end_stream()
        self.console.print(Text(f"  ↻ {event.text}", style=STYLE_WARN))

    def _on_error(self, event: AgentEvent) -> None:
        self._end_stream()
        self.console.print(Text(f"  ✗ {event.text}", style=STYLE_FAIL))

    def _on_subagent_start(self, event: AgentEvent) -> None:
        self._end_stream()
        role = event.data.get("role", "general")
        self.console.print(Text(f"  ⤷ subagent [{role}] {event.text}", style=STYLE_SKILL))

    def _on_subagent_end(self, event: AgentEvent) -> None:
        self.console.print(Text(f"  ⤶ {event.text}", style=STYLE_DIM))

    def _on_turn_end(self, event: AgentEvent) -> None:
        self._end_stream()
        result = event.data.get("result")
        if result is None:
            return
        content = (result.content or "").strip()
        already_shown = bool(self._stream_chars) or content == self._shown_text
        if content and not already_shown:
            self.console.print()
            self.print_markdown(content)

        if self.config.ui.show_timings:
            bits = [
                f"{result.iterations} iterations",
                f"{result.tool_calls} tool calls",
                f"{result.duration_s:.1f}s",
            ]
            if result.failed_tool_calls:
                bits.append(f"{result.failed_tool_calls} failed")
            if result.prompt_tokens or result.completion_tokens:
                bits.append(
                    f"{result.prompt_tokens + result.completion_tokens:,} tokens"
                )
            self.console.print()
            self.console.print(Text("  " + " · ".join(bits), style=STYLE_DIM))
        if result.skills_created:
            self.console.print(
                Text(
                    "  skills: " + ", ".join(result.skills_created),
                    style=STYLE_SKILL,
                )
            )
        self.console.print()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _first_line(text: str, limit: int = 140) -> str:
        line = (text or "").strip().splitlines()[0] if text.strip() else ""
        return line[:limit] + ("..." if len(line) > limit else "")

    def _summarize(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if not arguments:
            return ""
        for key in ("command", "path", "name", "pattern", "symbol", "query", "url", "source", "process_id"):
            value = arguments.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                text = str(value).replace("\n", " ⏎ ")
                return text[:100] + ("..." if len(text) > 100 else "")
        if "tasks" in arguments:
            tasks = arguments["tasks"]
            return f"{len(tasks)} subagent task(s)" if isinstance(tasks, list) else ""
        first = next(iter(arguments.values()), "")
        return str(first)[:80]

    @staticmethod
    def _result_detail(tool_name: str, result: ToolResult) -> str:
        data = result.data or {}
        if tool_name == "read_file" and "total_lines" in data:
            return f"{data.get('end', 0) - data.get('start', 1) + 1} lines"
        if tool_name in ("write_file",) and "lines" in data:
            return f"{'created' if data.get('created') else 'updated'}, {data['lines']} lines"
        if tool_name == "edit_file" and "replacements" in data:
            return f"{data['replacements']} replacement(s)"
        if tool_name == "run_command":
            return f"exit 0 in {data.get('duration_ms', 0) / 1000:.1f}s"
        if tool_name in ("search_text", "search_code") and "matches" in data:
            return f"{data['matches']} match(es)"
        if tool_name == "find_files" and "count" in data:
            return f"{data['count']} file(s)"
        if tool_name == "list_directory":
            return f"{data.get('directories', 0)} dirs, {data.get('files', 0)} files"
        if tool_name in ("create_skill", "update_skill") and "name" in data:
            return f"{data['name']} v{data.get('version', 1)}"
        if tool_name == "list_skills" and "count" in data:
            return f"{data['count']} skill(s)"
        if tool_name == "web_search" and "count" in data:
            return f"{data['count']} result(s)"
        first = Renderer._first_line(result.output, 70)
        return first

    def _failure_preview(self, result: ToolResult) -> list[str]:
        lines: list[str] = []
        limit = max(2, self.config.ui.tool_output_preview_lines)
        if result.hint:
            lines.append(f"hint: {self._first_line(result.hint, 160)}")
        body = (result.output or "").strip()
        if body:
            tail = [ln for ln in body.splitlines() if ln.strip()][-limit:]
            lines += [ln[:160] for ln in tail]
        return lines[: limit + 1]
