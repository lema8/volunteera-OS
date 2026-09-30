"""Provider adapter tests against a real local HTTP server."""

from __future__ import annotations

import json

import pytest

from fake_server import (
    FakeAPIServer,
    anthropic_message,
    ollama_message,
    openai_message,
    openai_tool_call,
)
from lema.config.schema import ProviderConfig
from lema.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    available_providers,
    create_provider,
    register_provider,
)
from lema.providers.base import (
    ContextOverflowError,
    Message,
    ModelProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderRateLimitError,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolSpec,
)
from lema.providers.text_tools import (
    flatten_for_text_protocol,
    parse_tool_calls,
    render_tools_prompt,
)


@pytest.fixture
def server():
    api = FakeAPIServer()
    url = api.start()
    api.url = url  # type: ignore[attr-defined]
    yield api
    api.stop()


def openai_config(url: str, **kwargs) -> ProviderConfig:
    options = {"stream": False, **kwargs}
    return ProviderConfig(
        name="test",
        kind="openai",
        model="test-model",
        base_url=url + "/v1",
        api_key="sk-test-key-value-12345678",
        **options,
    )


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="Read a file",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


# ------------------------------------------------------------------ registry


def test_provider_registry_knows_the_built_in_adapters():
    kinds = available_providers()
    assert "openai" in kinds
    assert "anthropic" in kinds
    assert "ollama" in kinds
    assert "openrouter" in kinds


def test_create_provider_selects_the_adapter():
    assert isinstance(create_provider(ProviderConfig(kind="openai")), OpenAICompatibleProvider)
    assert isinstance(create_provider(ProviderConfig(kind="anthropic")), AnthropicProvider)
    assert isinstance(create_provider(ProviderConfig(kind="ollama")), OllamaProvider)


def test_unknown_provider_kind_is_a_clear_error():
    with pytest.raises(ProviderError, match="unknown provider kind"):
        create_provider(ProviderConfig(kind="quantum-llm"))


def test_custom_providers_can_be_registered():
    class MyProvider(ModelProvider):
        kind = "mine"

        async def chat(self, *a, **kw):  # pragma: no cover
            raise NotImplementedError

    register_provider("mine", MyProvider)
    assert isinstance(create_provider(ProviderConfig(kind="mine")), MyProvider)


def test_api_key_resolution_order(monkeypatch):
    monkeypatch.setenv("MY_CUSTOM_KEY", "from-env")
    config = ProviderConfig(name="p", kind="openai", api_key_env="MY_CUSTOM_KEY")
    assert config.resolve_api_key() == "from-env"
    config.api_key = "explicit"
    assert config.resolve_api_key() == "explicit"


# -------------------------------------------------------------------- openai


@pytest.mark.asyncio
async def test_openai_plain_completion(server):
    server.reply_json(openai_message("Hello from the model."))
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        response = await provider.chat([Message.user("hi")])
    finally:
        await provider.aclose()

    assert response.content == "Hello from the model."
    assert response.usage.prompt_tokens == 11
    assert response.finish_reason == "stop"
    body = server.requests[0]["body"]
    assert body["model"] == "test-model"
    assert body["messages"][0] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_openai_sends_the_api_key(server):
    server.reply_json(openai_message("ok"))
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        await provider.chat([Message.user("hi")])
    finally:
        await provider.aclose()
    auth = [h.get("Authorization") for h in server.headers_seen if h.get("Authorization")]
    assert auth and auth[0] == "Bearer sk-test-key-value-12345678"


@pytest.mark.asyncio
async def test_openai_native_tool_calls_are_parsed(server):
    server.reply_json(
        openai_message(
            "", [openai_tool_call("call_1", "read_file", {"path": "src/main.py"})]
        )
    )
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        response = await provider.chat([Message.user("read it")], [READ_FILE_SPEC])
    finally:
        await provider.aclose()

    assert len(response.tool_calls) == 1
    tool_call = response.tool_calls[0]
    assert tool_call.name == "read_file"
    assert tool_call.arguments == {"path": "src/main.py"}
    assert tool_call.id == "call_1"
    # The tool schema was actually sent.
    sent = server.requests[0]["body"]
    assert sent["tools"][0]["function"]["name"] == "read_file"
    assert sent["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_openai_invalid_tool_arguments_are_flagged_not_crashed(server):
    server.reply_json(
        openai_message(
            "",
            [
                {
                    "id": "call_bad",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{not json"},
                }
            ],
        )
    )
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        response = await provider.chat([Message.user("x")], [READ_FILE_SPEC])
    finally:
        await provider.aclose()
    assert response.tool_calls[0].parse_error is not None
    assert "invalid JSON" in response.tool_calls[0].parse_error


@pytest.mark.asyncio
async def test_openai_streaming_text_and_tool_calls(server):
    server.reply_sse(
        [
            {"model": "test-model", "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
            {"model": "test-model", "choices": [{"index": 0, "delta": {"content": "lo!"}}]},
            {
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_s",
                                    "function": {"name": "read_file", "arguments": '{"pa'},
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": 'th": "a.txt"}'}}
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        ]
    )
    events: list[StreamEvent] = []
    provider = OpenAICompatibleProvider(openai_config(server.url, stream=True))
    try:
        response = await provider.chat(
            [Message.user("hi")], [READ_FILE_SPEC], stream=True, on_event=events.append
        )
    finally:
        await provider.aclose()

    assert response.content == "Hello!"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].arguments == {"path": "a.txt"}
    text_events = [e for e in events if e.type is StreamEventType.TEXT]
    assert "".join(e.text for e in text_events) == "Hello!"
    assert any(e.type is StreamEventType.TOOL_CALL_START for e in events)


@pytest.mark.asyncio
async def test_openai_assistant_tool_calls_are_serialised_for_the_next_turn(server):
    server.reply_json(openai_message("done"))
    provider = OpenAICompatibleProvider(openai_config(server.url))
    messages = [
        Message.user("read a.txt"),
        Message.assistant("", [ToolCall(name="read_file", arguments={"path": "a.txt"}, id="c1")]),
        Message.tool("file contents", "c1", "read_file"),
    ]
    try:
        await provider.chat(messages, [READ_FILE_SPEC])
    finally:
        await provider.aclose()

    sent = server.requests[0]["body"]["messages"]
    assert sent[1]["tool_calls"][0]["id"] == "c1"
    assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "file contents"}


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (401, {"error": {"message": "bad key"}}, ProviderAuthError),
        (429, {"error": {"message": "slow down"}}, ProviderRateLimitError),
        (500, {"error": {"message": "boom"}}, ProviderError),
        (400, {"error": {"message": "maximum context length exceeded"}}, ContextOverflowError),
    ],
)
@pytest.mark.asyncio
async def test_openai_error_mapping(server, status, payload, expected):
    server.reply_error(status, payload)
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        with pytest.raises(expected):
            await provider.chat([Message.user("x")])
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_openai_retryable_flags():
    config = ProviderConfig(kind="openai", base_url="http://127.0.0.1:1/v1", timeout=2)
    provider = OpenAICompatibleProvider(config)
    try:
        with pytest.raises(ProviderConnectionError) as exc:
            await provider.chat([Message.user("x")])
        assert exc.value.retryable is True
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_openai_list_models(server):
    provider = OpenAICompatibleProvider(openai_config(server.url))
    try:
        models = await provider.list_models()
    finally:
        await provider.aclose()
    assert models == ["other-model", "test-model"]


# ----------------------------------------------------------------- anthropic


@pytest.mark.asyncio
async def test_anthropic_content_blocks_and_tool_use(server):
    server.reply_json(
        anthropic_message(
            [
                {"type": "text", "text": "Let me read that."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_file",
                    "input": {"path": "x.py"},
                },
            ],
            stop_reason="tool_use",
        )
    )
    config = ProviderConfig(
        kind="anthropic", model="test-model", base_url=server.url, api_key="sk-ant-xyz", stream=False
    )
    provider = AnthropicProvider(config)
    try:
        response = await provider.chat([Message.user("read x.py")], [READ_FILE_SPEC])
    finally:
        await provider.aclose()

    assert response.content == "Let me read that."
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "x.py"}
    assert response.usage.prompt_tokens == 13

    body = server.requests[0]["body"]
    assert body["tools"][0]["input_schema"]["properties"]["path"]["type"] == "string"
    assert "max_tokens" in body


@pytest.mark.asyncio
async def test_anthropic_system_prompt_is_hoisted(server):
    server.reply_json(anthropic_message([{"type": "text", "text": "ok"}]))
    config = ProviderConfig(kind="anthropic", model="m", base_url=server.url, stream=False)
    provider = AnthropicProvider(config)
    try:
        await provider.chat([Message.system("be helpful"), Message.user("hi")])
    finally:
        await provider.aclose()
    body = server.requests[0]["body"]
    assert body["system"] == "be helpful"
    assert body["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_anthropic_merges_consecutive_tool_results(server):
    server.reply_json(anthropic_message([{"type": "text", "text": "ok"}]))
    config = ProviderConfig(kind="anthropic", model="m", base_url=server.url, stream=False)
    provider = AnthropicProvider(config)
    messages = [
        Message.user("do two things"),
        Message.assistant(
            "",
            [
                ToolCall(name="read_file", arguments={"path": "a"}, id="t1"),
                ToolCall(name="read_file", arguments={"path": "b"}, id="t2"),
            ],
        ),
        Message.tool("A", "t1", "read_file"),
        Message.tool("B", "t2", "read_file"),
    ]
    try:
        await provider.chat(messages)
    finally:
        await provider.aclose()

    sent = server.requests[0]["body"]["messages"]
    # Both tool results must be in ONE user message, per the Anthropic API.
    assert len(sent) == 3
    assert len(sent[2]["content"]) == 2
    assert {b["tool_use_id"] for b in sent[2]["content"]} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_anthropic_streaming(server):
    server.reply_sse(
        [
            {"type": "message_start", "message": {"model": "m", "usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tu1", "name": "read_file"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":"z.py"}'},
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},
        ]
    )
    config = ProviderConfig(kind="anthropic", model="m", base_url=server.url, stream=True)
    provider = AnthropicProvider(config)
    try:
        response = await provider.chat([Message.user("hi")], [READ_FILE_SPEC], stream=True)
    finally:
        await provider.aclose()

    assert response.content == "Hi there"
    assert response.tool_calls[0].arguments == {"path": "z.py"}
    assert response.usage.completion_tokens == 9


# -------------------------------------------------------------------- ollama


@pytest.mark.asyncio
async def test_ollama_native_tool_call(server):
    server.reply_json(
        ollama_message(
            "", [{"function": {"name": "read_file", "arguments": {"path": "main.go"}}}]
        )
    )
    config = ProviderConfig(
        kind="ollama", model="test-model", base_url=server.url, stream=False, tool_mode="native"
    )
    provider = OllamaProvider(config)
    try:
        response = await provider.chat([Message.user("read")], [READ_FILE_SPEC])
    finally:
        await provider.aclose()

    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "main.go"}
    body = next(r for r in server.requests if r["path"].endswith("/api/chat"))["body"]
    assert body["options"]["num_ctx"] == config.context_window


@pytest.mark.asyncio
async def test_ollama_capability_probe_selects_text_protocol(server):
    """A model without tool support must fall back to the text protocol."""
    # The fake /api/show always reports tools; override by forcing text mode.
    server.reply_json(
        ollama_message(
            'Sure.\n```tool_call\n{"name": "read_file", "arguments": {"path": "t.txt"}}\n```'
        )
    )
    config = ProviderConfig(
        kind="ollama", model="tiny", base_url=server.url, stream=False, tool_mode="text"
    )
    provider = OllamaProvider(config)
    try:
        response = await provider.chat([Message.user("read t.txt")], [READ_FILE_SPEC])
    finally:
        await provider.aclose()

    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "t.txt"}
    assert response.content.strip() == "Sure."
    # The tool manual was injected into the system prompt.
    body = next(r for r in server.requests if r["path"].endswith("/api/chat"))["body"]
    system = body["messages"][0]
    assert system["role"] == "system"
    assert "Tool calling protocol" in system["content"]
    assert "read_file" in system["content"]


@pytest.mark.asyncio
async def test_ollama_streaming_ndjson(server):
    server.reply_ndjson(
        [
            {"model": "m", "message": {"role": "assistant", "content": "par"}},
            {"model": "m", "message": {"role": "assistant", "content": "tial"}},
            {
                "model": "m",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
        ]
    )
    config = ProviderConfig(kind="ollama", model="m", base_url=server.url, stream=True)
    provider = OllamaProvider(config)
    try:
        response = await provider.chat([Message.user("hi")], stream=True)
    finally:
        await provider.aclose()
    assert response.content == "partial"
    assert response.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_ollama_missing_model_is_actionable(server):
    server.reply_error(404, {"error": "model not found"})
    config = ProviderConfig(kind="ollama", model="ghost:7b", base_url=server.url, stream=False)
    provider = OllamaProvider(config)
    try:
        with pytest.raises(ProviderError, match="ollama pull ghost:7b"):
            await provider.chat([Message.user("x")])
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_ollama_normalises_a_v1_base_url(server):
    config = ProviderConfig(kind="ollama", model="m", base_url=server.url + "/v1")
    provider = OllamaProvider(config)
    try:
        assert provider._base_url == server.url
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_ollama_connection_error_mentions_ollama_serve():
    config = ProviderConfig(kind="ollama", model="m", base_url="http://127.0.0.1:1", timeout=2)
    provider = OllamaProvider(config)
    try:
        with pytest.raises(ProviderConnectionError, match="ollama serve"):
            await provider.chat([Message.user("x")])
    finally:
        await provider.aclose()


# --------------------------------------------------------- text tool protocol


def test_render_tools_prompt_includes_schemas():
    prompt = render_tools_prompt([READ_FILE_SPEC])
    assert "```tool_call" in prompt
    assert "read_file" in prompt
    assert '"path"' in prompt


@pytest.mark.parametrize(
    "raw",
    [
        '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        '```tool\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        '```tool_call\n{"tool": "read_file", "parameters": {"path": "a.py"}}\n```',
    ],
)
def test_parse_tool_calls_tolerates_format_variation(raw):
    prose, parsed = parse_tool_calls("Sure thing.\n" + raw)
    assert len(parsed) == 1
    assert parsed[0].name == "read_file"
    assert parsed[0].arguments == {"path": "a.py"}
    assert prose.strip() == "Sure thing."


def test_parse_tool_calls_handles_multiple_blocks():
    text = (
        '```tool_call\n{"name": "a", "arguments": {}}\n```\n'
        '```tool_call\n{"name": "b", "arguments": {"x": 1}}\n```'
    )
    _, parsed = parse_tool_calls(text)
    assert [c.name for c in parsed] == ["a", "b"]


def test_parse_tool_calls_reports_broken_json():
    _, parsed = parse_tool_calls('```tool_call\n{"name": "a", "arg\n```')
    assert parsed and parsed[0].parse_error


def test_parse_tool_calls_returns_nothing_for_plain_prose():
    prose, parsed = parse_tool_calls("I have finished the task.")
    assert parsed == []
    assert prose == "I have finished the task."


def test_flatten_for_text_protocol_rewrites_tool_traffic():
    messages = [
        Message.system("sys"),
        Message.user("do it"),
        Message.assistant("working", [ToolCall(name="read_file", arguments={"path": "a"}, id="1")]),
        Message.tool("contents", "1", "read_file"),
    ]
    flat = flatten_for_text_protocol(messages)
    assert all(m.role.value in ("system", "user", "assistant") for m in flat)
    assert "```tool_call" in flat[2].content
    assert "TOOL RESULT [read_file]" in flat[3].content


def test_flatten_merges_consecutive_tool_results():
    messages = [
        Message.assistant(
            "",
            [
                ToolCall(name="a", arguments={}, id="1"),
                ToolCall(name="b", arguments={}, id="2"),
            ],
        ),
        Message.tool("A", "1", "a"),
        Message.tool("B", "2", "b"),
    ]
    flat = flatten_for_text_protocol(messages)
    user_messages = [m for m in flat if m.role.value == "user"]
    assert len(user_messages) == 1
    assert "TOOL RESULT [a]" in user_messages[0].content
    assert "TOOL RESULT [b]" in user_messages[0].content
