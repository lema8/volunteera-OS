"""Anthropic Messages API adapter."""

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

DEFAULT_VERSION = "2023-06-01"


class AnthropicProvider(ModelProvider):
    kind = "anthropic"

    def __init__(self, config: Any):
        super().__init__(config)
        base = config.effective_base_url()
        if not base.endswith("/v1"):
            base += "/v1"
        self._base_url = base
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": DEFAULT_VERSION,
        }
        key = self.config.resolve_api_key()
        if key:
            headers["x-api-key"] = key
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
        if "prompt is too long" in lowered or (
            "max_tokens" in lowered and "exceed" in lowered
        ):
            raise ContextOverflowError(f"context window exceeded: {body[:400]}")
        if status >= 500:
            raise ProviderError(f"server error ({status}): {body[:400]}", retryable=True, status=status)
        raise ProviderError(f"request failed ({status}): {body[:400]}", status=status)

    # ------------------------------------------------------------- payloads

    @staticmethod
    def _split_system(messages: Sequence[Message]) -> tuple[str, list[Message]]:
        system_parts: list[str] = []
        rest: list[Message] = []
        for msg in messages:
            if msg.role is Role.SYSTEM:
                system_parts.append(msg.content)
            else:
                rest.append(msg)
        return "\n\n".join(p for p in system_parts if p), rest

    @classmethod
    def _messages_payload(cls, messages: Sequence[Message]) -> list[dict[str, Any]]:
        """Convert to Anthropic's content-block format.

        Consecutive tool results must be merged into a single user message,
        which is what the loop below does.
        """
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role is Role.TOOL:
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "(no output)",
                }
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue

            if msg.role is Role.ASSISTANT:
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for call in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments or {},
                        }
                    )
                if not blocks:
                    blocks.append({"type": "text", "text": "(no content)"})
                out.append({"role": "assistant", "content": blocks})
                continue

            out.append({"role": "user", "content": [{"type": "text", "text": msg.content or ""}]})
        return out

    @staticmethod
    def _tools_payload(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

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
        system, rest = self._split_system(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages_payload(rest),
            "max_tokens": max_tokens or self.config.max_tokens or 4096,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = self._tools_payload(tools)
        if self.config.top_p is not None:
            body["top_p"] = self.config.top_p
        if self.config.extra_body:
            body.update(self.config.extra_body)

        do_stream = self.config.stream if stream is None else stream
        if do_stream:
            return await self._chat_stream(body, on_event)
        return await self._chat_once(body)

    async def _chat_once(self, body: dict[str, Any]) -> ChatResponse:
        client = await self._get_client()
        try:
            response = await client.post("/messages", json={**body, "stream": False})
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(f"cannot reach {self._base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"request timed out: {exc}") from exc
        self._raise_for_status(response, response.text)
        data = response.json()

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                        id=block.get("id") or "",
                    )
                )
        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("input_tokens") or 0),
            completion_tokens=int(usage_raw.get("output_tokens") or 0),
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=calls,
            finish_reason=data.get("stop_reason") or "stop",
            usage=usage,
            model=data.get("model") or self.model,
            raw=data,
        )

    async def _chat_stream(
        self, body: dict[str, Any], on_event: StreamHandler | None
    ) -> ChatResponse:
        client = await self._get_client()
        text_parts: list[str] = []
        blocks: dict[int, dict[str, Any]] = {}
        stop_reason = "stop"
        usage = Usage()
        model = self.model

        try:
            async with client.stream("POST", "/messages", json={**body, "stream": True}) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    self._raise_for_status(response, raw)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "message_start":
                        msg = event.get("message") or {}
                        model = msg.get("model") or model
                        u = msg.get("usage") or {}
                        usage.prompt_tokens = int(u.get("input_tokens") or 0)
                    elif etype == "content_block_start":
                        idx = event.get("index", 0)
                        block = event.get("content_block") or {}
                        blocks[idx] = {
                            "type": block.get("type"),
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "json": "",
                        }
                        if block.get("type") == "tool_use":
                            await emit(
                                on_event,
                                StreamEvent(
                                    StreamEventType.TOOL_CALL_START,
                                    tool_name=block.get("name", ""),
                                ),
                            )
                    elif etype == "content_block_delta":
                        idx = event.get("index", 0)
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            piece = delta.get("text", "")
                            text_parts.append(piece)
                            await emit(on_event, StreamEvent(StreamEventType.TEXT, text=piece))
                        elif delta.get("type") == "input_json_delta":
                            slot = blocks.setdefault(idx, {"type": "tool_use", "json": ""})
                            slot["json"] = slot.get("json", "") + delta.get("partial_json", "")
                    elif etype == "message_delta":
                        delta = event.get("delta") or {}
                        stop_reason = delta.get("stop_reason") or stop_reason
                        u = event.get("usage") or {}
                        if u.get("output_tokens"):
                            usage.completion_tokens = int(u["output_tokens"])
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(f"cannot reach {self._base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderConnectionError(f"stream timed out: {exc}") from exc

        calls: list[ToolCall] = []
        for idx in sorted(blocks):
            slot = blocks[idx]
            if slot.get("type") != "tool_use":
                continue
            parse_error = None
            raw_json = slot.get("json", "")
            try:
                arguments = json.loads(raw_json) if raw_json.strip() else {}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except json.JSONDecodeError as exc:
                arguments = {}
                parse_error = f"model emitted invalid JSON arguments: {exc}"
            calls.append(
                ToolCall(
                    name=slot.get("name") or "",
                    arguments=arguments,
                    id=slot.get("id") or f"call_{idx}",
                    parse_error=parse_error,
                )
            )

        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        await emit(on_event, StreamEvent(StreamEventType.DONE))
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=calls,
            finish_reason=stop_reason,
            usage=usage,
            model=model,
        )

    async def list_models(self) -> list[str]:
        client = await self._get_client()
        try:
            response = await client.get("/models")
        except httpx.HTTPError as exc:
            raise ProviderConnectionError(f"cannot reach {self._base_url}: {exc}") from exc
        if response.status_code == 404:
            # Older gateways do not expose /models.
            return []
        if response.status_code >= 400:
            self._raise_for_status(response, response.text)
        data = response.json()
        return sorted(
            str(item.get("id"))
            for item in (data.get("data") or [])
            if isinstance(item, dict) and item.get("id")
        )
