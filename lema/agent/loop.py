"""The agent loop.

    user task
        -> model
            -> tool calls?  no  -> final response
                            yes -> execute -> results -> model -> ...

Everything else in this module exists to make that loop survive contact with
reality: provider hiccups, context overflow, models that call tools that do
not exist, models that loop, and users who press Ctrl-C.

The loop has no knowledge of any specific tool or provider.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from lema.agent.compaction import CompactionResult, compact
from lema.agent.context import ContextManager
from lema.agent.events import AgentEvent, EventHandler, EventType, emit_event
from lema.config.schema import Config
from lema.providers.base import (
    ChatResponse,
    ContextOverflowError,
    Message,
    ModelProvider,
    ProviderError,
    Role,
    StreamEvent,
    StreamEventType,
    ToolCall,
)
from lema.tools.base import FailureKind, ToolContext, ToolResult
from lema.tools.git import repo_summary
from lema.tools.registry import ToolRegistry


#: Why a turn ended.
STOP_COMPLETED = "completed"
STOP_MAX_ITERATIONS = "max_iterations"
STOP_INTERRUPTED = "interrupted"
STOP_FATAL = "fatal_error"
STOP_NO_PROGRESS = "no_progress"


@dataclass
class TurnResult:
    """Outcome of one user turn."""

    content: str
    stop_reason: str = STOP_COMPLETED
    iterations: int = 0
    tool_calls: int = 0
    failed_tool_calls: int = 0
    duration_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    skills_created: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    compactions: list[CompactionResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stop_reason == STOP_COMPLETED and self.error is None


class Interrupt(Exception):
    """Raised internally when the user interrupts the turn."""


class Agent:
    """The runtime that drives a model through tools to complete a task."""

    def __init__(
        self,
        config: Config,
        provider: ModelProvider,
        tools: ToolRegistry,
        context: ContextManager,
        tool_context: ToolContext,
        *,
        on_event: EventHandler | None = None,
        allowed_tools: Sequence[str] | None = None,
        max_iterations: int | None = None,
        system_prompt_override: str | None = None,
    ):
        self.config = config
        self.provider = provider
        self.tools = tools
        self.context = context
        self.tool_context = tool_context
        self.on_event = on_event
        self.allowed_tools = list(allowed_tools) if allowed_tools is not None else None
        self.max_iterations = max_iterations or config.agent.max_iterations
        self.system_prompt_override = system_prompt_override

        self._interrupt = asyncio.Event()
        self._running = False
        #: Fingerprints of recent tool calls, for loop detection.
        self._recent_calls: list[str] = []

    # --------------------------------------------------------------- state

    @property
    def running(self) -> bool:
        return self._running

    def interrupt(self) -> None:
        """Ask the loop to stop at the next safe point."""
        self._interrupt.set()

    def _check_interrupt(self) -> None:
        if self._interrupt.is_set():
            raise Interrupt()

    async def _emit(self, event: AgentEvent) -> None:
        await emit_event(self.on_event, event)

    # ----------------------------------------------------------- git state

    async def _refresh_git(self) -> None:
        if not self.config.context.include_git_status:
            return
        try:
            summary = await repo_summary(self.tool_context.cwd, self.config.tools.shell)
        except Exception:  # noqa: BLE001 - git state is a nicety, never fatal
            return
        if not summary.get("is_repo"):
            self.context.set_git_summary("")
            return
        parts = [f"branch: {summary['branch']}"]
        if summary.get("last_commit"):
            parts.append(f"last commit: {summary['last_commit']}")
        if summary["clean"]:
            parts.append("working tree: clean")
        else:
            bits = []
            if summary["staged"]:
                bits.append(f"{len(summary['staged'])} staged")
            if summary["modified"]:
                bits.append(f"{len(summary['modified'])} modified")
            if summary["untracked"]:
                bits.append(f"{len(summary['untracked'])} untracked")
            parts.append("working tree: " + ", ".join(bits))
            changed = (summary["staged"] + summary["modified"])[:12]
            if changed:
                parts.append("changed files: " + ", ".join(changed))
        self.context.set_git_summary("\n".join(parts))

    # -------------------------------------------------------- model call

    async def _call_model(self, messages: list[Message], iteration: int) -> ChatResponse:
        """One model call, with retry for transient failures and overflow."""
        specs = self.tools.specs(allowed=self.allowed_tools)
        attempts = 0
        max_attempts = 4
        delay = 1.0

        while True:
            attempts += 1
            self._check_interrupt()

            streamed = False

            async def handle_stream(event: StreamEvent) -> None:
                nonlocal streamed
                if event.type is StreamEventType.TEXT and event.text:
                    streamed = True
                    await self._emit(
                        AgentEvent(EventType.TEXT_DELTA, text=event.text, iteration=iteration)
                    )
                elif event.type is StreamEventType.TOOL_CALL_START and event.tool_name:
                    await self._emit(
                        AgentEvent(
                            EventType.NOTICE,
                            text=f"calling {event.tool_name}",
                            tool_name=event.tool_name,
                            iteration=iteration,
                        )
                    )

            try:
                if self.tool_context.logger is not None:
                    self.tool_context.logger.log_model_request(
                        self.provider.describe(), messages, len(specs)
                    )
                response = await self.provider.chat(
                    messages,
                    specs,
                    on_event=handle_stream if self.config.ui.stream_output else None,
                    stream=self.config.ui.stream_output and self.config.provider.stream,
                )
                if self.tool_context.logger is not None:
                    self.tool_context.logger.log_model_response(response)
                return response

            except ContextOverflowError as exc:
                if attempts >= 3:
                    raise
                await self._emit(
                    AgentEvent(
                        EventType.WARNING,
                        text=f"context window exceeded; compacting and retrying ({exc})",
                        iteration=iteration,
                    )
                )
                result = await self._compact(force=True)
                if result is None or result.removed_messages == 0:
                    raise
                messages[:] = self._build_messages()

            except ProviderError as exc:
                if not exc.retryable or attempts >= max_attempts:
                    raise
                await self._emit(
                    AgentEvent(
                        EventType.RETRY,
                        text=f"{exc} - retrying in {delay:.0f}s (attempt {attempts}/{max_attempts})",
                        iteration=iteration,
                    )
                )
                try:
                    await asyncio.wait_for(self._interrupt.wait(), timeout=delay)
                    raise Interrupt() from exc
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 16.0)

    def _build_messages(self) -> list[Message]:
        messages = self.context.build_request()
        if self.system_prompt_override:
            messages = [Message.system(self.system_prompt_override), *self.context.messages]
        return messages

    # -------------------------------------------------------- compaction

    async def _compact(self, *, force: bool = False) -> CompactionResult | None:
        if not force and not self.context.needs_compaction():
            return None
        stats = self.context.stats()
        new_messages, result = await compact(
            self.context.messages,
            self.provider,
            keep_recent=self.config.context.keep_recent_messages,
            system_prompt_tokens=stats.system_tokens,
        )
        if result.removed_messages == 0:
            return result
        self.context.messages = new_messages
        self.context.compactions += 1
        if self.tool_context.logger is not None:
            self.tool_context.logger.log_event("compaction", {"summary": result.render()})
        await self._emit(AgentEvent(EventType.COMPACTION, text=result.render(), data={"result": result}))
        return result

    # ------------------------------------------------------- tool calling

    def _fingerprint(self, call: ToolCall) -> str:
        try:
            args = json.dumps(call.arguments, sort_keys=True, default=str)
        except (TypeError, ValueError):
            args = str(call.arguments)
        return f"{call.name}:{args}"

    def _detect_loop(self, calls: Sequence[ToolCall]) -> str | None:
        """Warn the model when it repeats an identical call with no progress."""
        for call in calls:
            fingerprint = self._fingerprint(call)
            self._recent_calls.append(fingerprint)
        self._recent_calls = self._recent_calls[-12:]
        if len(self._recent_calls) < 3:
            return None
        last = self._recent_calls[-1]
        repeats = sum(1 for f in self._recent_calls[-4:] if f == last)
        if repeats >= 3:
            name = last.split(":", 1)[0]
            return (
                f"You have now called `{name}` with identical arguments {repeats} times. "
                "Repeating an unchanged call cannot produce a different result. "
                "Stop, re-read the last error carefully, and either change the approach "
                "or explain to the user what is blocking you."
            )
        return None

    async def _execute_tools(
        self, calls: Sequence[ToolCall], iteration: int
    ) -> tuple[list[Message], int]:
        """Run the requested tool calls and build the resulting messages."""
        limit = self.config.agent.max_tool_calls_per_turn
        overflow: list[ToolCall] = []
        if len(calls) > limit:
            overflow = list(calls[limit:])
            calls = calls[:limit]

        # Calls whose arguments failed to parse never reach the registry.
        runnable: list[tuple[str, str, dict[str, Any]]] = []
        messages: list[Message] = []
        prepared: dict[str, ToolCall] = {}
        failures = 0

        for call in calls:
            if call.parse_error:
                result = ToolResult.failure(
                    call.parse_error,
                    FailureKind.INVALID_INPUT,
                    hint=(
                        "Emit valid JSON for the tool arguments. "
                        "Check the tool's schema and try again."
                    ),
                )
                failures += 1
                await self._emit(
                    AgentEvent(
                        EventType.TOOL_END,
                        tool_name=call.name,
                        tool_call_id=call.id,
                        result=result,
                        iteration=iteration,
                    )
                )
                messages.append(
                    Message.tool(
                        result.render_for_model(self.config.context.max_tool_result_chars),
                        call.id,
                        call.name,
                    )
                )
                continue
            prepared[call.id] = call
            runnable.append((call.id, call.name, call.arguments))
            await self._emit(
                AgentEvent(
                    EventType.TOOL_START,
                    tool_name=call.name,
                    tool_call_id=call.id,
                    arguments=call.arguments,
                    iteration=iteration,
                )
            )

        self._check_interrupt()

        results = await self.tools.dispatch_many(
            runnable, self.tool_context, parallel=self.config.agent.parallel_tool_calls
        )

        for call_id, result in results:
            call = prepared[call_id]
            if not result.ok:
                failures += 1
            await self._emit(
                AgentEvent(
                    EventType.TOOL_END,
                    tool_name=call.name,
                    tool_call_id=call_id,
                    arguments=call.arguments,
                    result=result,
                    iteration=iteration,
                )
            )
            messages.append(
                Message.tool(
                    result.render_for_model(self.config.context.max_tool_result_chars),
                    call_id,
                    call.name,
                    touched=result.metadata.get("touched", []),
                )
            )

        for call in overflow:
            messages.append(
                Message.tool(
                    f"ERROR [invalid_input]: not executed - you requested more than "
                    f"{limit} tool calls in one response. Re-issue this call in your next turn.",
                    call.id,
                    call.name,
                )
            )

        return messages, failures

    def _handle_skill_events(self) -> list[str]:
        """Pick up skill changes made by tools during this iteration."""
        events = self.tool_context.scratch.pop("skill_events", [])
        created: list[str] = []
        for event in events:
            action = event.get("action")
            name = event.get("name")
            if action in ("created", "updated") and name:
                created.append(name)
                self.context.add_notice(
                    f"Skill '{name}' was {action} during this session and is loaded and usable now."
                )
        if events:
            # Force the system prompt to be rebuilt with the new skill index.
            self.context.invalidate_system_prompt()
        return created

    # ---------------------------------------------------------------- run

    async def run(self, task: str) -> TurnResult:
        """Execute one user turn to completion."""
        started = time.perf_counter()
        self._running = True
        self._interrupt.clear()
        self._recent_calls = []

        result = TurnResult(content="", iterations=0)
        await self._emit(AgentEvent(EventType.TURN_START, text=task))

        if self.tool_context.logger is not None:
            self.tool_context.logger.log_event("user_message", {"text": task})

        await self._refresh_git()
        self.context.add_user(task)

        consecutive_errors = 0
        final_text = ""

        try:
            for iteration in range(1, self.max_iterations + 1):
                result.iterations = iteration
                self._check_interrupt()
                await self._emit(AgentEvent(EventType.ITERATION_START, iteration=iteration))

                await self._compact()

                messages = self._build_messages()
                await self._emit(
                    AgentEvent(
                        EventType.MODEL_REQUEST,
                        iteration=iteration,
                        data={"messages": len(messages)},
                    )
                )

                try:
                    response = await self._call_model(messages, iteration)
                except Interrupt:
                    raise
                except ProviderError as exc:
                    consecutive_errors += 1
                    await self._emit(
                        AgentEvent(EventType.ERROR, text=str(exc), iteration=iteration)
                    )
                    if consecutive_errors >= self.config.agent.max_consecutive_errors:
                        result.stop_reason = STOP_FATAL
                        result.error = str(exc)
                        final_text = final_text or f"Stopped: {exc}"
                        break
                    continue

                consecutive_errors = 0
                result.prompt_tokens += response.usage.prompt_tokens
                result.completion_tokens += response.usage.completion_tokens

                assistant = response.to_message()
                self.context.add(assistant)

                if response.content.strip():
                    await self._emit(
                        AgentEvent(
                            EventType.ASSISTANT_MESSAGE,
                            text=response.content,
                            iteration=iteration,
                        )
                    )

                if not response.tool_calls:
                    final_text = response.content.strip()
                    if not final_text:
                        # A completely empty response is a provider/model glitch;
                        # nudge once rather than ending the turn silently.
                        if consecutive_errors == 0 and iteration < self.max_iterations:
                            self.context.add(
                                Message.user(
                                    "Your last response was empty. Continue the task, "
                                    "or state clearly that it is complete."
                                )
                            )
                            consecutive_errors += 1
                            continue
                        final_text = "(the model returned an empty response)"
                    result.stop_reason = STOP_COMPLETED
                    break

                result.tool_calls += len(response.tool_calls)
                tool_messages, failures = await self._execute_tools(
                    response.tool_calls, iteration
                )
                result.failed_tool_calls += failures
                self.context.extend(tool_messages)

                created = self._handle_skill_events()
                for name in created:
                    if name not in result.skills_created:
                        result.skills_created.append(name)
                    await self._emit(
                        AgentEvent(
                            EventType.SKILL_EVENT,
                            text=f"skill '{name}' loaded and available now",
                            data={"skill": name},
                        )
                    )

                warning = self._detect_loop(response.tool_calls)
                if warning:
                    self.context.add(Message.user(warning))
                    await self._emit(AgentEvent(EventType.WARNING, text=warning, iteration=iteration))

            else:
                result.stop_reason = STOP_MAX_ITERATIONS
                final_text = final_text or (
                    f"Reached the iteration limit ({self.max_iterations}) without finishing. "
                    "Increase agent.max_iterations or narrow the task."
                )
                await self._emit(
                    AgentEvent(EventType.WARNING, text=final_text, iteration=result.iterations)
                )

        except Interrupt:
            result.stop_reason = STOP_INTERRUPTED
            final_text = final_text or "Interrupted by user."
            # Leave the transcript coherent for the next turn.
            self._close_dangling_tool_calls()
        except asyncio.CancelledError:
            result.stop_reason = STOP_INTERRUPTED
            self._close_dangling_tool_calls()
            raise
        except ProviderError as exc:
            result.stop_reason = STOP_FATAL
            result.error = str(exc)
            final_text = f"Provider error: {exc}"
            await self._emit(AgentEvent(EventType.ERROR, text=str(exc)))
        finally:
            self._running = False

        result.content = final_text
        result.duration_s = time.perf_counter() - started
        touched = self.tool_context.scratch.get("touched_files", {})
        result.files_touched = sorted(touched)

        if self.tool_context.logger is not None:
            self.tool_context.logger.log_event(
                "turn_end",
                {
                    "stop_reason": result.stop_reason,
                    "iterations": result.iterations,
                    "tool_calls": result.tool_calls,
                    "failed_tool_calls": result.failed_tool_calls,
                    "duration_s": round(result.duration_s, 2),
                    "skills_created": result.skills_created,
                },
            )

        await self._emit(
            AgentEvent(
                EventType.TURN_END,
                text=final_text,
                data={"result": result},
                iteration=result.iterations,
            )
        )
        return result

    def _close_dangling_tool_calls(self) -> None:
        """Ensure every assistant tool_call has a matching tool message.

        Providers reject a transcript where an assistant asked for a tool and
        no result followed, which is exactly what an interrupt produces.
        """
        answered: set[str] = {
            m.tool_call_id for m in self.context.messages if m.role is Role.TOOL and m.tool_call_id
        }
        additions: list[Message] = []
        for message in self.context.messages:
            if message.role is not Role.ASSISTANT:
                continue
            for call in message.tool_calls:
                if call.id not in answered:
                    additions.append(
                        Message.tool(
                            "ERROR [interrupted]: the user interrupted execution before "
                            "this tool ran.",
                            call.id,
                            call.name,
                        )
                    )
                    answered.add(call.id)
        self.context.extend(additions)
