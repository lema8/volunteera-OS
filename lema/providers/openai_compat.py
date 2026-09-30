"""OpenAI-compatible chat completions adapter.

Works against OpenAI itself, OpenRouter, Groq, Together, llama.cpp's server,
vLLM, LM Studio, LocalAI, Ollama's ``/v1`` shim, and anything else that speaks
``POST /chat/completions``.
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
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
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

_CONTEXT_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
)


class OpenAICompatibleProvider(ModelProvider):
    kind = "openai"

    def __init__(self, config: Any):
        super().__init__(config)
        self._base_url = config.effective_base_url()
        # Tolerate users configuring either "https://host" or "https://host/v1".
        if not self._base_url.endswith("/v1") and "openai.com" in self._base_url:
            self._base_url += "/v1"
        self._client: httpx.AsyncClient | None = None
        self._native_tools: bool | None = None
        if config.tool_mode == "native":
            self._native_tools = True
        elif config.tool_mode == "text":
            self._native_tools = False

    # ------------------------------------------------------------------ http

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.config.resolve_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(self.config.extra_headers or {})
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=15.0),
                headers=self._headers(),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _raise_for_status(response: httpx.Response, body: str) -> None:
        status = response.status_code
        if status < 400:
            return
        lowered = body.lower()
        if status in (401, 403):
            raise ProviderAuthError(f"authentication failed ({status}): {body[:400]}", status)
        if status == 429:
            raise ProviderRateLimitError(f"rate limited: {body[:400]}")
        if any(marker in lowered for marker in _CONTEXT_MARKERS):
            raise ContextOverflowError(f"context window exceeded: {body[:400]}")
        if status >= 500:
            raise ProviderError(f"server error ({status}): {body[:400]}", retryable=True, status=status)
        raise ProviderError(f"request failed ({status}): {body[:400]}", status=status)

    # -------------------------------------------------------------- payloads

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

    @staticmethod
    def _messages_payload(messages: Sequence[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role is Role.TOOL:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content,
                    }
                )
                continue
            entry: dict[str, Any] = {"role": msg.role.value, "content": msg.content or ""}
            if msg.role is Role.ASSISTANT and msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, default=str),
                        },
                    }
                    for tc in msg.tool_calls
                ]
                # OpenAI rejects null content alongside tool_calls on some models.
                entry["content"] = msg.content or ""
            out.append(entry)
        return out

    def _prepare(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, bool]:
        """Return (messages_payload, tools_payload, used_native_tools)."""
        use_native = self.supports_native_tools if self._native_tools is None else self._native_tools
        if not tools:
            return self._messages_payload(messages), None, use_native

        if use_native:
            return self._messages_payload(messages), self._tools_payload(tools), True

        # Text protocol: append the tool manual to the system message.
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
        return self._messages_payload(merged), None, False

    # ------------------------------------------------------------------ chat

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
        payload_messages, payload_tools, native = self._prepare(messages, tools)
        do_stream = self.config.stream if stream is None else stream

        body: dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        limit = self.config.max_tokens if max_tokens is None else max_tokens
        if limit:
            body["max_tokens"] = limit
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p
        if payload_tools:
            body["tools"] = payload_tools
            body["tool_choice"] = "auto"
        if self.config.extra_body:
            body.update(self.config.extra_body)

        if do_stream:
            try:
                return await self._chat_stream(body, on_event, native)
            except ProviderError:
                raise
        return await self._chat_once(body, native)

    async def _request(self, body: dict[str, Any]) -> httpx.Response:
        client = await self._get_client()
        try:
            return await client.post("/chat/completions", json=body)
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                f"cannot reach {self._base_url}: {exc}. Is the server running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"request to {self._base_url} timed out: {exc}") from exc
        except httpx.HTTPError as exc:  # pragma: no cover - transport edge cases
            raise ProviderConnectionError(f"HTTP error talking to {self._base_url}: {exc}") from exc

    async def _chat_once(self, body: dict[str, Any], native: bool) -> ChatResponse:
        body = {**body, "stream": False}
        response = await self._request(body)
        text = response.text
        self._raise_for_status(response, text)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"malformed JSON response: {text[:400]}") from exc
        return self._parse_response(data, native)

    def _parse_response(self, data: dict[str, Any], native: bool) -> ChatResponse:
        choices = data.get("choices") or []
        if not choices:
            error = data.get("error")
            if error:
                raise ProviderError(f"provider error: {error}")
            raise ProviderError(f"response contained no choices: {str(data)[:300]}")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            name = fn.get("name") or ""
            args_raw = fn.get("arguments")
            parse_error = None
            if isinstance(args_raw, dict):
                arguments = args_raw
            else:
                try:
                    arguments = json.loads(args_raw) if args_raw else {}
                    if not isinstance(arguments, dict):
                        arguments = {"value": arguments}
                except json.JSONDecodeError as exc:
                    arguments = {}
                    parse_error = f"model emitted invalid JSON arguments: {exc}. Raw: {str(args_raw)[:200]}"
            calls.append(
                ToolCall(
                    name=name,
                    arguments=arguments,
                    id=raw.get("id") or f"call_{len(calls)}",
                    parse_error=parse_error,
                )
            )

        if not native and not calls:
            content, calls = parse_tool_calls(content)

        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
        )
        return ChatResponse(
            content=content or "",
            tool_calls=calls,
            finish_reason=choice.get("finish_reason") or "stop",
            usage=usage,
            model=data.get("model") or self.model,
            raw=data,
        )

    async def _chat_stream(
        self, body: dict[str, Any], on_event: StreamHandler | None, native: bool
    ) -> ChatResponse:
        body = {**body, "stream": True}
        client = await self._get_client()
        text_parts: list[str] = []
        # index -> partial tool call
        partial: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage = Usage()
        model = self.model
        announced: set[int] = set()

        try:
            async with client.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    self._raise_for_status(response, raw)
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    model = data.get("model") or model
                    if data.get("usage"):
                        u = data["usage"]
                        usage = Usage(
                            int(u.get("prompt_tokens") or 0),
                            int(u.get("completion_tokens") or 0),
                            int(u.get("total_tokens") or 0),
                        )
                    for choice in data.get("choices") or []:
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            if isinstance(piece, list):
                                piece = "".join(
                                    p.get("text", "") for p in piece if isinstance(p, dict)
                                )
                            text_parts.append(piece)
                            await emit(on_event, StreamEvent(StreamEventType.TEXT, text=piece))
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = partial.setdefault(
                                idx, {"id": None, "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                                if idx not in announced and slot["name"]:
                                    announced.add(idx)
                                    await emit(
                                        on_event,
                                        StreamEvent(
                                            StreamEventType.TOOL_CALL_START,
                                            tool_name=slot["name"],
                                        ),
                                    )
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                f"cannot reach {self._base_url}: {exc}. Is the server running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"stream from {self._base_url} timed out: {exc}") from exc

        content = "".join(text_parts)
        calls: list[ToolCall] = []
        for idx in sorted(partial):
            slot = partial[idx]
            if not slot["name"]:
                continue
            parse_error = None
            try:
                arguments = json.loads(slot["arguments"]) if slot["arguments"].strip() else {}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except json.JSONDecodeError as exc:
                arguments = {}
                parse_error = (
                    f"model emitted invalid JSON arguments: {exc}. "
                    f"Raw: {slot['arguments'][:200]}"
                )
            calls.append(
                ToolCall(
                    name=slot["name"],
                    arguments=arguments,
                    id=slot["id"] or f"call_{idx}",
                    parse_error=parse_error,
                )
            )

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

    # ---------------------------------------------------------------- health

    async def list_models(self) -> list[str]:
        client = await self._get_client()
        try:
            response = await client.get("/models")
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(f"cannot reach {self._base_url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(f"HTTP error: {exc}") from exc
        if response.status_code >= 400:
            self._raise_for_status(response, response.text)
        try:
            data = response.json()
        except json.JSONDecodeError:
            return []
        items = data.get("data") if isinstance(data, dict) else data
        out = []
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(str(item["id"]))
            elif isinstance(item, str):
                out.append(item)
        return sorted(out)
