"""Interactive REPL.

Uses prompt_toolkit for a real line editor (history, multi-line, completion)
and rich for output.  Ctrl-C interrupts the *agent* rather than killing the
process, which is what you want when a long task goes wrong.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console

from lema.agent.permissions import ApprovalRequest, ApprovalScope
from lema.cli.commands import COMMANDS, dispatch
from lema.cli.renderer import Renderer
from lema.config.schema import PermissionMode
from lema.storage.history import history_file
from lema.util.paths import shorten_path

if TYPE_CHECKING:  # pragma: no cover
    from lema.session import Session

PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "ansicyan bold",
        "path": "ansibrightblack",
        "continuation": "ansibrightblack",
    }
)


class LemaCompleter(Completer):
    """Completes slash commands, skill names and paths."""

    def __init__(self, session_ref: Any):
        self.session_ref = session_ref

    def get_completions(self, document, complete_event):  # type: ignore[no-untyped-def]
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        parts = text[1:].split()
        # Completing the command itself.
        if len(parts) <= 1 and not text.endswith(" "):
            prefix = parts[0] if parts else ""
            for name, command in sorted(COMMANDS.items()):
                if name.startswith(prefix):
                    yield Completion(
                        name, start_position=-len(prefix), display_meta=command.summary
                    )
            return

        command_name = parts[0]
        current = "" if text.endswith(" ") else parts[-1]

        session = self.session_ref()
        if session is None:
            return

        if command_name == "skills":
            if len(parts) == 1 or (len(parts) == 2 and not text.endswith(" ")):
                for sub in ("reload", "show", "new"):
                    if sub.startswith(current):
                        yield Completion(sub, start_position=-len(current))
            for skill in session.skills.all():
                if skill.name.startswith(current):
                    yield Completion(
                        skill.name,
                        start_position=-len(current),
                        display_meta=skill.description[:50],
                    )
        elif command_name == "model":
            for name, profile in sorted(session.config.providers.items()):
                if name.startswith(current):
                    yield Completion(
                        name, start_position=-len(current), display_meta=profile.model
                    )
        elif command_name in ("permissions", "config"):
            for mode in PermissionMode:
                if mode.value.startswith(current):
                    yield Completion(mode.value, start_position=-len(current))
        elif command_name == "cd":
            base = Path(current).expanduser()
            directory = base if base.is_dir() else base.parent
            try:
                for entry in sorted(directory.iterdir()):
                    if entry.is_dir() and entry.name.startswith(base.name if not base.is_dir() else ""):
                        yield Completion(
                            str(entry), start_position=-len(current), display=entry.name + "/"
                        )
            except OSError:
                return


class Repl:
    """The interactive loop."""

    def __init__(self, session: "Session", console: Console | None = None):
        self.session = session
        self.renderer = Renderer(session.config, console)
        self._agent_task: asyncio.Task[Any] | None = None
        self._interrupt_count = 0

        bindings = KeyBindings()

        @bindings.add("escape", "enter")
        def _(event):  # type: ignore[no-untyped-def]
            """Alt-Enter inserts a newline for multi-line prompts."""
            event.current_buffer.insert_text("\n")

        self.prompt_session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file())),
            completer=LemaCompleter(lambda: self.session),
            style=PROMPT_STYLE,
            key_bindings=bindings,
            complete_while_typing=True,
            enable_history_search=True,
        )

    # --------------------------------------------------------- approvals

    async def approval_callback(self, request: ApprovalRequest) -> ApprovalScope:
        """Prompt the user to approve a mutating tool call."""
        console = self.renderer.console
        console.print()
        console.print(f"[yellow]▸ approval required[/yellow] [cyan]{request.tool_name}[/cyan]")
        console.print(f"  {request.summary}")
        if request.dangerous:
            console.print("  [red]this tool can execute arbitrary code[/red]")
        console.print(
            "  [grey50]y=once  t=always this tool  c=always this category  "
            "a=allow everything  n=deny  N=never this tool[/grey50]"
        )

        try:
            answer = await self.prompt_session.prompt_async(
                HTML("  <prompt>allow?</prompt> [y/N] "),
                default="",
            )
        except (EOFError, KeyboardInterrupt):
            return ApprovalScope.DENY

        answer = (answer or "").strip()
        mapping = {
            "y": ApprovalScope.ONCE,
            "yes": ApprovalScope.ONCE,
            "t": ApprovalScope.TOOL,
            "c": ApprovalScope.CATEGORY,
            "a": ApprovalScope.SESSION,
            "all": ApprovalScope.SESSION,
            "N": ApprovalScope.DENY_TOOL,
        }
        if answer in mapping:
            return mapping[answer]
        if answer.lower() in mapping:
            return mapping[answer.lower()]
        return ApprovalScope.DENY

    # -------------------------------------------------------------- loop

    def _prompt_fragments(self) -> HTML:
        cwd = shorten_path(self.session.tool_context.cwd)
        return HTML(f"<path>{cwd}</path>\n<prompt>❯</prompt> ")

    async def run_once(self, task: str) -> None:
        """Run one agent turn, allowing Ctrl-C to interrupt it."""
        agent = self.session.agent
        self._interrupt_count = 0

        loop = asyncio.get_running_loop()
        previous_handler: Any = None

        def on_sigint() -> None:
            self._interrupt_count += 1
            if self._interrupt_count == 1:
                agent.interrupt()
                self.renderer.console.print(
                    "\n[yellow]interrupting... (press Ctrl-C again to force)[/yellow]"
                )
            else:
                if self._agent_task is not None:
                    self._agent_task.cancel()

        try:
            loop.add_signal_handler(signal.SIGINT, on_sigint)
        except (NotImplementedError, RuntimeError):  # pragma: no cover
            previous_handler = None

        self._agent_task = asyncio.create_task(agent.run(task))
        try:
            await self._agent_task
        except asyncio.CancelledError:
            self.renderer.warning("cancelled")
        finally:
            self._agent_task = None
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):  # pragma: no cover
                pass

    async def run(self, initial_task: str | None = None) -> int:
        """Main REPL loop. Returns a process exit code."""
        session = self.session
        self.renderer.banner(
            model=session.config.provider.model,
            provider=session.config.provider.kind,
            cwd=str(session.tool_context.cwd),
            skills=len(session.skills),
            tools=len(session.tools),
        )

        for warning in session.startup_warnings:
            self.renderer.warning(warning)
        if session.startup_warnings:
            self.renderer.console.print()

        health = await session.provider.health()
        if not health.ok:
            self.renderer.warning(f"provider not reachable: {health.detail}")
            self.renderer.info(
                f"  endpoint: {session.config.provider.effective_base_url()} · "
                f"model: {session.config.provider.model}"
            )
            self.renderer.info("  fix it with /model, or /config path to see the config file")
            self.renderer.console.print()
        elif health.models and session.config.provider.model not in health.models:
            close = [m for m in health.models if session.config.provider.model.split(":")[0] in m]
            self.renderer.warning(
                f"model '{session.config.provider.model}' was not listed by the endpoint"
            )
            if close:
                self.renderer.info(f"  similar available: {', '.join(close[:5])}")
            self.renderer.console.print()

        self.renderer.info("Type /help for commands, or just describe what you want done.")
        self.renderer.console.print()

        if initial_task:
            await self.run_once(initial_task)

        while True:
            try:
                with patch_stdout(raw=True):
                    line = await self.prompt_session.prompt_async(self._prompt_fragments())
            except KeyboardInterrupt:
                self.renderer.info("(Ctrl-D or /exit to quit)")
                continue
            except EOFError:
                self.renderer.console.print()
                break

            line = (line or "").strip()
            if not line:
                continue

            if line.startswith("/"):
                result = await dispatch(self, line)
                if result.exit:
                    break
                if result.forward_to_agent:
                    await self.run_once(result.forward_to_agent)
                if result.handled:
                    continue

            if line.startswith("!"):
                # Shell escape - run a command directly without the model.
                await self._run_shell_escape(line[1:])
                continue

            await self.run_once(line)

        return 0

    async def _run_shell_escape(self, command: str) -> None:
        from lema.tools.shell import run_shell

        command = command.strip()
        if not command:
            return
        result = await run_shell(
            command,
            cwd=self.session.tool_context.cwd,
            timeout=self.session.config.tools.shell_timeout,
            shell=self.session.config.tools.shell,
        )
        if result["stdout"]:
            self.renderer.console.print(result["stdout"].rstrip(), markup=False, highlight=False)
        if result["stderr"]:
            self.renderer.console.print(
                result["stderr"].rstrip(), markup=False, highlight=False, style="red"
            )
        if result["exit_code"] != 0:
            self.renderer.error(f"exit {result['exit_code']}")
