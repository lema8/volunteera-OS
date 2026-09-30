"""Subagents.

A subagent is a real, independent agent loop with its own context window,
its own (usually narrowed) tool set, and its own iteration budget.  It shares
the parent's provider, tool registry and skill registry, so a skill created by
a subagent is immediately available to everyone.

Parallelism here is genuine ``asyncio`` concurrency, not simulated: when the
model requests several tasks they run as concurrent tasks against a provider
that is safe to use from multiple coroutines.

Depth is bounded by ``agent.max_subagent_depth`` so a runaway model cannot
fork indefinitely.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Sequence

from lema.agent.context import ContextManager
from lema.agent.events import AgentEvent, EventHandler, EventType, emit_event
from lema.agent.prompts import build_subagent_prompt
from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)

#: Curated tool sets for common subagent roles.
ROLE_TOOLSETS: dict[str, list[str]] = {
    "research": [
        "read_file",
        "list_directory",
        "search_text",
        "find_files",
        "search_code",
        "git_log",
        "git_diff",
        "git_status",
        "web_search",
        "fetch_url",
        "list_skills",
        "read_skill",
    ],
    "review": [
        "read_file",
        "list_directory",
        "search_text",
        "search_code",
        "find_files",
        "git_diff",
        "git_status",
        "git_log",
        "run_command",
    ],
    "test": [
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "search_text",
        "find_files",
        "run_command",
        "which",
    ],
    "code": None,  # everything
    "general": None,
}

ROLE_DESCRIPTIONS = {
    "research": "Investigate and report. You cannot modify the project; gather facts and explain them.",
    "review": "Review code for correctness, clarity and risk. Report findings with file:line references.",
    "test": "Write and run tests. Report exactly which tests pass and which fail, with output.",
    "code": "Implement the requested change end to end, and verify it works.",
    "general": "Complete the task using whatever tools are appropriate.",
}


@dataclass
class SubagentResult:
    task: str
    role: str
    content: str
    ok: bool
    iterations: int = 0
    tool_calls: int = 0
    error: str | None = None
    duration_s: float = 0.0
    skills_created: list[str] = field(default_factory=list)


async def run_subagent(
    *,
    task: str,
    role: str,
    parent_ctx: ToolContext,
    provider: Any,
    tools: Any,
    config: Any,
    on_event: EventHandler | None = None,
    max_iterations: int | None = None,
    allowed_tools: Sequence[str] | None = None,
) -> SubagentResult:
    """Run one subagent to completion and return its report."""
    from lema.agent.loop import Agent  # local import: avoids a cycle

    if parent_ctx.depth >= config.agent.max_subagent_depth:
        return SubagentResult(
            task=task,
            role=role,
            content="",
            ok=False,
            error=(
                f"subagent depth limit reached ({config.agent.max_subagent_depth}); "
                "complete this task directly instead of delegating further"
            ),
        )

    if allowed_tools is None:
        allowed_tools = ROLE_TOOLSETS.get(role, None)

    # A fresh context: the subagent gets the task, not the parent's history.
    sub_context = ContextManager(config, parent_ctx.skills, cwd=parent_ctx.cwd)
    skills = sub_context.select_task_skills(task)
    system_prompt = build_subagent_prompt(
        config,
        cwd=str(parent_ctx.cwd),
        role=ROLE_DESCRIPTIONS.get(role, role),
        project_summary=sub_context.project.summary(),
        skill_bodies="\n\n".join(s.render() for s in skills),
    )

    sub_tool_ctx = ToolContext(
        config=config,
        cwd=parent_ctx.cwd,
        project_root=parent_ctx.project_root,
        permissions=parent_ctx.permissions,
        registry=tools,
        skills=parent_ctx.skills,
        processes=parent_ctx.processes,
        logger=parent_ctx.logger,
        depth=parent_ctx.depth + 1,
        scratch=parent_ctx.scratch,  # shared: file/skill tracking must roll up
    )

    agent = Agent(
        config,
        provider,
        tools,
        sub_context,
        sub_tool_ctx,
        on_event=on_event,
        allowed_tools=allowed_tools,
        max_iterations=max_iterations or config.agent.subagent_max_iterations,
        system_prompt_override=system_prompt,
    )

    turn = await agent.run(task)
    return SubagentResult(
        task=task,
        role=role,
        content=turn.content,
        ok=turn.ok,
        iterations=turn.iterations,
        tool_calls=turn.tool_calls,
        error=turn.error,
        duration_s=turn.duration_s,
        skills_created=turn.skills_created,
    )


class TaskTool(Tool):
    """Delegate work to one or more subagents."""

    name = "task"
    description = (
        "Delegate a self-contained task to a subagent with its own context window. "
        "Use this for work that would otherwise flood your context - exploring an "
        "unfamiliar area of a large codebase, reviewing a diff, or running a long "
        "investigation. Pass several tasks to run them concurrently.\n\n"
        "The subagent returns only its final report, so state the task precisely and "
        "say what the report must contain. Do not delegate work you can do in one or "
        "two tool calls."
    )
    category = ToolCategory.AGENT
    mutating = True
    read_only = False
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "One or more tasks to run (concurrently when there are several).",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Complete, self-contained instructions including what to report back.",
                        },
                        "role": {
                            "type": "string",
                            "enum": ["research", "review", "test", "code", "general"],
                            "description": "Determines which tools the subagent gets.",
                            "default": "general",
                        },
                    },
                    "required": ["task"],
                },
            }
        },
        "required": ["tasks"],
    }

    def __init__(self, provider: Any, tools: Any, on_event: EventHandler | None = None):
        self.provider = provider
        self.tools = tools
        self.on_event = on_event
        super().__init__()

    def summarize_call(self, arguments: dict[str, Any]) -> str:
        tasks = arguments.get("tasks") or []
        if len(tasks) == 1 and isinstance(tasks[0], dict):
            text = str(tasks[0].get("task", ""))[:70]
            return f"task ({tasks[0].get('role', 'general')}): {text}"
        return f"task × {len(tasks)} subagents"

    async def run(self, ctx: ToolContext, tasks: list[dict[str, Any]]) -> ToolResult:
        if not ctx.config.agent.subagents_enabled:
            raise ToolError(
                "subagents are disabled (agent.subagents_enabled = false)",
                FailureKind.CONFIGURATION,
                hint="Do this task yourself.",
            )
        if not tasks:
            raise ToolError("no tasks provided", FailureKind.INVALID_INPUT)

        normalized: list[tuple[str, str]] = []
        for entry in tasks:
            if isinstance(entry, str):
                normalized.append((entry, "general"))
                continue
            if not isinstance(entry, dict):
                raise ToolError(
                    f"each task must be an object with a 'task' field, got {type(entry).__name__}",
                    FailureKind.INVALID_INPUT,
                )
            text = str(entry.get("task") or "").strip()
            if not text:
                raise ToolError("a task entry has an empty 'task' field", FailureKind.INVALID_INPUT)
            role = str(entry.get("role") or "general")
            if role not in ROLE_TOOLSETS:
                role = "general"
            normalized.append((text, role))

        for text, role in normalized:
            await emit_event(
                self.on_event,
                AgentEvent(
                    EventType.SUBAGENT_START,
                    text=text[:120],
                    data={"role": role, "depth": ctx.depth + 1},
                ),
            )

        results = await asyncio.gather(
            *(
                run_subagent(
                    task=text,
                    role=role,
                    parent_ctx=ctx,
                    provider=self.provider,
                    tools=self.tools,
                    config=ctx.config,
                    on_event=None,  # subagent chatter stays out of the main stream
                )
                for text, role in normalized
            ),
            return_exceptions=True,
        )

        sections: list[str] = []
        any_failed = False
        payload: list[dict[str, Any]] = []

        for (text, role), outcome in zip(normalized, results):
            if isinstance(outcome, BaseException):
                any_failed = True
                sections.append(
                    f"### subagent ({role}) FAILED\nTask: {text}\n"
                    f"Error: {type(outcome).__name__}: {outcome}"
                )
                payload.append({"task": text, "role": role, "ok": False, "error": str(outcome)})
                continue

            await emit_event(
                self.on_event,
                AgentEvent(
                    EventType.SUBAGENT_END,
                    text=f"{role}: {'ok' if outcome.ok else 'failed'} "
                    f"({outcome.iterations} iterations, {outcome.tool_calls} tool calls)",
                    data={"role": role},
                ),
            )
            status = "completed" if outcome.ok else f"did not complete ({outcome.error or outcome.content[:100]})"
            if not outcome.ok:
                any_failed = True
            sections.append(
                f"### subagent ({role}) {status}\n"
                f"Task: {text}\n"
                f"Iterations: {outcome.iterations}, tool calls: {outcome.tool_calls}, "
                f"{outcome.duration_s:.1f}s\n\n"
                f"{outcome.content or '(no report returned)'}"
            )
            payload.append(
                {
                    "task": text,
                    "role": role,
                    "ok": outcome.ok,
                    "error": outcome.error,
                    "iterations": outcome.iterations,
                    "tool_calls": outcome.tool_calls,
                    "content": outcome.content,
                }
            )

        body = "\n\n".join(sections)
        succeeded = sum(1 for entry in payload if entry.get("ok"))
        if succeeded == 0:
            # Every subagent failed: report a failure so the parent model
            # treats it as an error to recover from rather than as findings.
            errors = "; ".join(
                str(entry.get("error") or "no report") for entry in payload
            )
            return ToolResult(
                ok=False,
                output=body,
                error=f"all {len(payload)} subagent(s) failed: {errors}"[:500],
                failure_kind=FailureKind.UNKNOWN,
                hint=(
                    "Do the work directly with your own tools, or restate the "
                    "task more concretely and delegate again."
                ),
                data={"results": payload, "partial_failure": True},
            )
        if any_failed:
            return ToolResult(
                ok=True,  # partial results are still useful to the parent
                output=body,
                data={"results": payload, "partial_failure": True},
            )
        return ToolResult.success(body, data={"results": payload})
