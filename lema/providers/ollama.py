"""Ollama adapter (native ``/api/chat`` endpoint).

Ollama also exposes an OpenAI-compatible ``/v1`` shim, but the native endpoint
gives us access to ``num_ctx``, model listing with parameter sizes, and better
streaming semantics - all of which matter when running small local models.

Native tool-calling support varies per model.  ``tool_mode = "auto"`` probes
the model's capabilities once (via ``/api/show``) and falls back to the text
protocol when the model does not advertise tools.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import httpx

from lema.providers.base import (
    ChatResponse,
    ContextOverflowError,
    Message,
    ModelProvider,
    ProviderConnectionError,
    ProviderError,
    Role,
    StreamEvent,
    StreamEventType,
    StreamHandler,
    ToolCall,
    ToolSpec,
    Usage,
    emit,
)
from lema.providers.text_tools import (
    flatten_for_text_protocol,
    parse_tool_calls,
    render_tools_prompt,
)


class OllamaProvider(ModelProvider):
    kind = "ollama"

    def __init__(self, config: Any):
        super().__init__(config)
        base = config.effective_base_url()
        # Users often paste the OpenAI shim URL; normalise back to the root.
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._base_url = base
        self._client: httpx.AsyncClient | None = None
        self._native_tools: bool | None = None
        if config.tool_mode == "native":
            self._native_tools = True
        elif config.tool_mode == "text":
            self._native_tools = False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            headers.update(self.config.extra_headers or {})
            key = self.config.resolve_api_key()
            if key:
                headers["Authorization"] = f"Bearer {key}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers=headers,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------- capability probe

    async def supports_tools(self) -> bool:
        """Ask Ollama whether this model advertises tool support."""
        if self._native_tools is not None:
            return self._native_tools
        client = await self._get_client()
        try:
            response = await client.post("/api/show", json={"model": self.model})
            if response.status_code >= 400:
                self._native_tools = False
            else:
                data = response.json()
                caps = data.get("capabilities") or []
                if caps:
                    self._native_tools = "tools" in caps
                else:
                    # Older Ollama builds have no `capabilities`; the template
                    # mentioning `.Tools` is the reliable signal.
                    template = data.get("template") or ""
                    self._native_tools = ".Tools" in template
        except (httpx.HTTPError, json.JSONDecodeError):
            self._native_tools = False
        return bool(self._native_tools)

    # ------------------------------------------------------------- payloads

    @staticmethod
    def _messages_payload(messages: Sequence[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role is Role.TOOL:
                entry: dict[str, Any] = {"role": "tool", "content": msg.content}
                if msg.name:
                    entry["tool_name"] = msg.name
                out.append(entry)
                continue
            entry = {"role": msg.role.value, "content": msg.content or ""}
            if msg.role is Role.ASSISTANT and msg.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in msg.tool_calls
                ]
            out.append(entry)
        return out

    @staticmethod
    def _tools_payload(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _options(self, temperature: float | None, max_tokens: int | None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": self.config.temperature if temperature is None else temperature,
            "num_ctx": self.config.context_window,
        }
        limit = self.config.max_tokens if max_tokens is None else max_tokens
        if limit:
            options["num_predict"] = limit
        if self.config.top_p is not None:
            options["top_p"] = self.config.top_p
        return options

    # ----------------------------------------------------------------- chat

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
        native = True
        payload_messages: list[dict[str, Any]]
        payload_tools: list[dict[str, Any]] | None = None

        if tools:
            native = await self.supports_tools()
            if native:
                payload_messages = self._messages_payload(messages)
                payload_tools = self._tools_payload(tools)
            else:
                prompt = render_tools_prompt(tools)
                flattened = flatten_for_text_protocol(messages)
                merged: list[Message] = []
                injected = False
                for msg in flattened:
                    if msg.role is Role.SYSTEM and not injected:
                        merged.append(Message.system(msg.content + "\n\n" + prompt))
                        injected = True
                    else:
                        merged.append(msg)
                if not injected:
                    merged.insert(0, Message.system(prompt))
                payload_messages = self._messages_payload(merged)
        else:
            payload_messages = self._messages_payload(messages)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "options": self._options(temperature, max_tokens),
        }
        if payload_tools:
            body["tools"] = payload_tools
        if self.config.extra_body:
            body.update(self.config.extra_body)

        do_stream = self.config.stream if stream is None else stream
        # Ollama disallows streaming together with tool calls on some versions;
        # streaming still works, but tool call deltas arrive whole.
        if do_stream:
            return await self._chat_stream(body, on_event, native)
        return await self._chat_once(body, native)

    def _parse_message(self, data: dict[str, Any], native: bool) -> ChatResponse:
        message = data.get("message") or {}
        content = message.get("content") or ""
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args or {}))
        if not native and not calls:
            content, calls = parse_tool_calls(content)
        usage = Usage(
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatResponse(
            content=content,
            tool_calls=calls,
            finish_reason=data.get("done_reason") or "stop",
            usage=usage,
            model=data.get("model") or self.model,
            raw=data,
        )

    async def _chat_once(self, body: dict[str, Any], native: bool) -> ChatResponse:
        client = await self._get_client()
        try:
            response = await client.post("/api/chat", json={**body, "stream": False})
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                f"cannot reach Ollama at {self._base_url}: {exc}. "
                "Start it with `ollama serve`."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"Ollama request timed out: {exc}") from exc
        self._check(response, response.text)
        return self._parse_message(response.json(), native)

    def _check(self, response: httpx.Response, body: str) -> None:
        if response.status_code < 400:
            return
        lowered = body.lower()
        if "context" in lowered and ("exceed" in lowered or "too long" in lowered):
            raise ContextOverflowError(f"context window exceeded: {body[:300]}")
        if response.status_code == 404:
            raise ProviderError(
                f"model {self.model!r} not found on {self._base_url}. "
                f"Pull it with `ollama pull {self.model}`.",
                status=404,
            )
        retryable = response.status_code >= 500
        raise ProviderError(
            f"Ollama error ({response.status_code}): {body[:300]}",
            retryable=retryable,
            status=response.status_code,
        )

    async def _chat_stream(
        self, body: dict[str, Any], on_event: StreamHandler | None, native: bool
    ) -> ChatResponse:
        client = await self._get_client()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage = Usage()
        finish_reason = "stop"
        model = self.model
        announced: set[str] = set()

        try:
            async with client.stream("POST", "/api/chat", json={**body, "stream": True}) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    self._check(response, raw)
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    model = event.get("model") or model
                    message = event.get("message") or {}
                    piece = message.get("content")
                    if piece:
                        text_parts.append(piece)
                        await emit(on_event, StreamEvent(StreamEventType.TEXT, text=piece))
                    for raw in message.get("tool_calls") or []:
                        fn = raw.get("function") or {}
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"_raw": args}
                        name = fn.get("name", "")
                        calls.append(ToolCall(name=name, arguments=args or {}))
                        if name not in announced:
                            announced.add(name)
                            await emit(
                                on_event,
                                StreamEvent(StreamEventType.TOOL_CALL_START, tool_name=name),
                            )
                    if event.get("done"):
                        finish_reason = event.get("done_reason") or "stop"
                        usage = Usage(
                            prompt_tokens=int(event.get("prompt_eval_count") or 0),
                            completion_tokens=int(event.get("eval_count") or 0),
                        )
                        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                f"cannot reach Ollama at {self._base_url}: {exc}. "
                "Start it with `ollama serve`."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"Ollama stream timed out: {exc}") from exc

        content = "".join(text_parts)
        if not native and not calls:
            content, calls = parse_tool_calls(content)

        await emit(on_event, StreamEvent(StreamEventType.DONE))
        return ChatResponse(
            content=content,
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=usage,
            model=model,
        )

    async def list_models(self) -> list[str]:
        client = await self._get_client()
        try:
            response = await client.get("/api/tags")
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(
                f"cannot reach Ollama at {self._base_url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            self._check(response, response.text)
        data = response.json()
        return sorted(
            str(m.get("name"))
            for m in (data.get("models") or [])
            if isinstance(m, dict) and m.get("name")
        )
