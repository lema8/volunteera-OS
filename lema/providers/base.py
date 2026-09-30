"""Provider-neutral model interface.

Everything above this module (agent loop, tools, skills, CLI) speaks only in
terms of :class:`Message`, :class:`ToolSpec` and :class:`ChatResponse`.  Adding
a new backend means implementing :class:`ModelProvider` and registering it - no
changes anywhere else.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Sequence


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A model's request to invoke one tool."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    #: Set when the provider emitted syntactically invalid arguments; the agent
    #: turns this into a tool error the model can recover from.
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Message:
    """One entry in the conversation."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: For role==TOOL: which call this is answering.
    tool_call_id: str | None = None
    #: For role==TOOL: the tool's name (some providers require it).
    name: str | None = None
    #: Harness-internal bookkeeping; never sent to a provider.
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def system(cls, content: str, **meta: Any) -> "Message":
        return cls(role=Role.SYSTEM, content=content, metadata=meta)

    @classmethod
    def user(cls, content: str, **meta: Any) -> "Message":
        return cls(role=Role.USER, content=content, metadata=meta)

    @classmethod
    def assistant(
        cls, content: str = "", tool_calls: Sequence[ToolCall] | None = None, **meta: Any
    ) -> "Message":
        return cls(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=list(tool_calls or []),
            metadata=meta,
        )

    @classmethod
    def tool(cls, content: str, tool_call_id: str, name: str, **meta: Any) -> "Message":
        return cls(
            role=Role.TOOL,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
            metadata=meta,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class ToolSpec:
    """Provider-neutral description of a callable tool."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )


@dataclass
class ChatResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    raw: dict[str, Any] | None = None

    def to_message(self) -> Message:
        return Message.assistant(self.content, self.tool_calls)


class StreamEventType(str, Enum):
    TEXT = "text"
    TOOL_CALL_START = "tool_call_start"
    DONE = "done"


@dataclass
class StreamEvent:
    type: StreamEventType
    text: str = ""
    tool_name: str = ""


#: Called with incremental output while a response is being generated.
StreamHandler = Callable[[StreamEvent], Awaitable[None] | None]


class ProviderError(Exception):
    """Base class for provider failures."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class ProviderAuthError(ProviderError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message, retryable=False, status=status)


class ProviderConnectionError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)


class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str, status: int | None = 429):
        super().__init__(message, retryable=True, status=status)


class ContextOverflowError(ProviderError):
    """The request exceeded the model's context window."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


@dataclass
class ProviderHealth:
    ok: bool
    detail: str = ""
    models: list[str] = field(default_factory=list)


class ModelProvider(ABC):
    """Abstract model backend.

    Implementations must be safe to use concurrently and must translate
    transport-level failures into the :class:`ProviderError` hierarchy so that
    the agent loop can decide whether to retry.
    """

    #: Adapter identifier, e.g. "openai".
    kind: str = "base"
    #: Whether the backend accepts structured tool definitions. When False the
    #: agent transparently falls back to the text tool protocol.
    supports_native_tools: bool = True

    def __init__(self, config: Any):
        self.config = config
        self.model = getattr(config, "model", "")
        self.context_window = getattr(config, "context_window", 32768)

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        stream: bool | None = None,
        on_event: StreamHandler | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Run one completion. Must not mutate ``messages``."""

    async def list_models(self) -> list[str]:
        """Model ids available at this endpoint (best effort)."""
        return []

    async def health(self) -> ProviderHealth:
        """Check that the endpoint is reachable and the model exists."""
        try:
            models = await self.list_models()
            return ProviderHealth(ok=True, detail="reachable", models=models)
        except ProviderError as exc:
            return ProviderHealth(ok=False, detail=str(exc))

    async def aclose(self) -> None:
        """Release network resources."""

    async def __aenter__(self) -> "ModelProvider":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def describe(self) -> str:
        return f"{self.kind}:{self.model}"


async def emit(handler: StreamHandler | None, event: StreamEvent) -> None:
    """Invoke a stream handler that may be sync or async."""
    if handler is None:
        return
    result = handler(event)
    if hasattr(result, "__await__"):
        await result  # type: ignore[misc]


async def collect_stream(iterator: AsyncIterator[StreamEvent]) -> str:
    parts: list[str] = []
    async for event in iterator:
        if event.type is StreamEventType.TEXT:
            parts.append(event.text)
    return "".join(parts)
