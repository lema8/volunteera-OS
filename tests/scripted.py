"""A programmable in-process model provider for tests.

This is *not* a fake tool executor: every tool the scripted model requests is
executed for real by the real registry against a real filesystem.  Only the
model's decision-making is replaced, which is exactly what you want when you
need deterministic tests of the agent runtime.

Two flavours:

* :class:`ScriptedProvider` - replays a fixed list of responses.
* :class:`PolicyProvider`   - decides each response from the conversation, so
  it can react to tool results like a real model would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from lema.providers.base import (
    ChatResponse,
    Message,
    ModelProvider,
    ProviderError,
    ProviderHealth,
    Role,
    StreamEvent,
    StreamEventType,
    StreamHandler,
    ToolCall,
    ToolSpec,
    Usage,
    emit,
)


@dataclass
class FakeProviderConfig:
    """Minimal stand-in for ProviderConfig."""

    model: str = "scripted-model"
    kind: str = "scripted"
    name: str = "scripted"
    base_url: str = "memory://"
    temperature: float = 0.0
    top_p: float | None = None
    max_tokens: int | None = 2048
    context_window: int = 32768
    timeout: float = 30.0
    tool_mode: str = "native"
    stream: bool = False
    api_key: str | None = None
    api_key_env: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    def resolve_api_key(self) -> str | None:
        return self.api_key


def text(content: str) -> ChatResponse:
    return ChatResponse(content=content, finish_reason="stop", usage=Usage(10, 5, 15))


def call(name: str, /, **arguments: Any) -> ToolCall:
    return ToolCall(name=name, arguments=arguments)


def calls(*tool_calls: ToolCall, content: str = "") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=list(tool_calls),
        finish_reason="tool_calls",
        usage=Usage(20, 10, 30),
    )


class RecordingProvider(ModelProvider):
    """Base class that records everything it is asked."""

    kind = "scripted"

    #: When True, compaction requests are answered with a canned summary
    #: instead of consuming a scripted turn. Compaction is an internal
    #: mechanism, so tests of the agent loop should not have to script it.
    auto_summarize = True

    def __init__(self, config: FakeProviderConfig | None = None):
        super().__init__(config or FakeProviderConfig())
        #: Every message list handed to chat(), in order.
        self.requests: list[list[Message]] = []
        #: Tool specs offered on each call.
        self.tool_specs: list[list[ToolSpec]] = []
        self.call_count = 0
        #: Compaction requests, kept out of `requests`.
        self.summary_requests: list[list[Message]] = []

    @property
    def last_request(self) -> list[Message]:
        return self.requests[-1]

    def system_prompts(self) -> list[str]:
        """The system prompt used for each request."""
        out = []
        for messages in self.requests:
            system = next((m.content for m in messages if m.role is Role.SYSTEM), "")
            out.append(system)
        return out

    def tool_names_offered(self) -> list[str]:
        return [s.name for s in (self.tool_specs[-1] if self.tool_specs else [])]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="scripted", models=[self.model])

    async def list_models(self) -> list[str]:
        return [self.model]

    async def _respond(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> ChatResponse:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def _is_compaction_request(messages: Sequence[Message]) -> bool:
        return bool(messages) and messages[0].role is Role.SYSTEM and (
            "You are compacting" in messages[0].content
        )

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
        if self.auto_summarize and self._is_compaction_request(messages):
            self.summary_requests.append(list(messages))
            return text(
                "## Task\n- (scripted summary)\n\n## Files\n- (none)\n\n"
                "## Outstanding\n- continue the work"
            )

        self.requests.append(list(messages))
        self.tool_specs.append(list(tools or []))
        self.call_count += 1
        response = await self._respond(messages, tools)
        if on_event is not None and response.content:
            await emit(on_event, StreamEvent(StreamEventType.TEXT, text=response.content))
        if on_event is not None:
            await emit(on_event, StreamEvent(StreamEventType.DONE))
        return response


class ScriptedProvider(RecordingProvider):
    """Replays a fixed sequence of responses."""

    def __init__(
        self,
        responses: Sequence[ChatResponse],
        config: FakeProviderConfig | None = None,
        *,
        loop_last: bool = False,
    ):
        super().__init__(config)
        self.responses = list(responses)
        self.loop_last = loop_last
        self._index = 0

    async def _respond(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> ChatResponse:
        if self._index >= len(self.responses):
            if self.loop_last and self.responses:
                return self.responses[-1]
            raise ProviderError("scripted provider exhausted: the agent asked for more turns than scripted")
        response = self.responses[self._index]
        self._index += 1
        return response


#: A policy receives (messages, tools, call_index) and returns a response.
Policy = Callable[[list[Message], list[ToolSpec], int], ChatResponse]


class PolicyProvider(RecordingProvider):
    """Decides each response from the conversation so far."""

    def __init__(self, policy: Policy, config: FakeProviderConfig | None = None):
        super().__init__(config)
        self.policy = policy

    async def _respond(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> ChatResponse:
        return self.policy(list(messages), list(tools or []), self.call_count - 1)


class FailingProvider(RecordingProvider):
    """Fails a number of times, then delegates to a fallback response."""

    def __init__(
        self,
        error: Exception,
        *,
        failures: int = 1,
        then: ChatResponse | None = None,
        config: FakeProviderConfig | None = None,
    ):
        super().__init__(config)
        self.error = error
        self.failures = failures
        self.then = then or text("recovered")
        self.attempts = 0

    async def _respond(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> ChatResponse:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise self.error
        return self.then


def last_tool_output(messages: Sequence[Message], tool_name: str | None = None) -> str:
    """Most recent tool result, optionally filtered by tool name."""
    for message in reversed(messages):
        if message.role is Role.TOOL and (tool_name is None or message.name == tool_name):
            return message.content
    return ""


def tool_outputs(messages: Sequence[Message], tool_name: str) -> list[str]:
    return [m.content for m in messages if m.role is Role.TOOL and m.name == tool_name]


def called_tools(messages: Sequence[Message]) -> list[str]:
    out: list[str] = []
    for message in messages:
        for tool_call in message.tool_calls:
            out.append(tool_call.name)
    return out
