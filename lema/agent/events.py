"""Events emitted by the agent loop.

The loop never touches the terminal directly: it emits events, and a renderer
(or a test, or a future web UI) decides what to do with them.  This keeps the
runtime headless and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from lema.tools.base import ToolResult


class EventType(str, Enum):
    TURN_START = "turn_start"
    ITERATION_START = "iteration_start"
    MODEL_REQUEST = "model_request"
    TEXT_DELTA = "text_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    SKILL_EVENT = "skill_event"
    COMPACTION = "compaction"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    RETRY = "retry"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"
    TURN_END = "turn_end"


@dataclass
class AgentEvent:
    type: EventType
    text: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: ToolResult | None = None
    iteration: int = 0
    data: dict[str, Any] = field(default_factory=dict)


#: Consumers implement this; may be sync or async.
EventHandler = Callable[[AgentEvent], Awaitable[None] | None]


async def emit_event(handler: EventHandler | None, event: AgentEvent) -> None:
    if handler is None:
        return
    result = handler(event)
    if hasattr(result, "__await__"):
        await result  # type: ignore[misc]
