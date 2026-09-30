"""Agent loop behaviour: iteration, recovery, limits, interruption."""

from __future__ import annotations

import asyncio

import pytest

from lema.agent.events import EventType
from lema.agent.loop import (
    STOP_COMPLETED,
    STOP_FATAL,
    STOP_INTERRUPTED,
    STOP_MAX_ITERATIONS,
    Agent,
)
from lema.providers.base import (
    ContextOverflowError,
    Message,
    ProviderConnectionError,
    ProviderError,
    Role,
    ToolCall,
)
from scripted import (
    FailingProvider,
    PolicyProvider,
    ScriptedProvider,
    call,
    calls,
    last_tool_output,
    text,
)


@pytest.mark.asyncio
async def test_simple_turn_without_tools(make_agent):
    provider = ScriptedProvider([text("Nothing to do.")])
    agent = make_agent(provider)
    result = await agent.run("say hi")
    assert result.ok
    assert result.content == "Nothing to do."
    assert result.iterations == 1
    assert result.tool_calls == 0


@pytest.mark.asyncio
async def test_multi_step_task_runs_to_completion(make_agent, workspace):
    provider = ScriptedProvider(
        [
            calls(call("write_file", path="a.txt", content="one\n")),
            calls(call("write_file", path="b.txt", content="two\n")),
            calls(call("list_directory", path=".")),
            text("Created a.txt and b.txt."),
        ]
    )
    agent = make_agent(provider)
    result = await agent.run("create two files")
    assert result.ok
    assert result.iterations == 4
    assert result.tool_calls == 3
    assert (workspace / "a.txt").exists()
    assert (workspace / "b.txt").exists()
    assert sorted(result.files_touched) == sorted(
        [str(workspace / "a.txt"), str(workspace / "b.txt")]
    )


@pytest.mark.asyncio
async def test_parallel_tool_calls_in_one_response(make_agent, workspace):
    provider = ScriptedProvider(
        [
            calls(
                call("write_file", path="p1.txt", content="1"),
                call("write_file", path="p2.txt", content="2"),
                call("write_file", path="p3.txt", content="3"),
            ),
            text("done"),
        ]
    )
    agent = make_agent(provider)
    result = await agent.run("write three files")
    assert result.ok
    assert result.tool_calls == 3
    for name in ("p1.txt", "p2.txt", "p3.txt"):
        assert (workspace / name).exists()
    # Each call gets its own tool message, matched by id.
    tool_messages = [m for m in agent.context.messages if m.role is Role.TOOL]
    assert len(tool_messages) == 3
    assert len({m.tool_call_id for m in tool_messages}) == 3


@pytest.mark.asyncio
async def test_agent_recovers_from_a_failing_command(make_agent, workspace):
    """Spec §11: diagnose, fix, retry - do not give up at the first error."""
    attempts: list[str] = []

    def policy(messages, tools, index):
        if index == 0:
            attempts.append("run-missing-script")
            return calls(call("run_command", command="python3 build_it.py"))
        if index == 1:
            output = last_tool_output(messages, "run_command")
            # The failure is classified for the model.
            assert "ERROR [" in output
            attempts.append("diagnose")
            return calls(call("list_directory", path="."))
        if index == 2:
            attempts.append("fix")
            return calls(
                call("write_file", path="build_it.py", content="print('built ok')\n")
            )
        if index == 3:
            attempts.append("retry")
            return calls(call("run_command", command="python3 build_it.py"))
        output = last_tool_output(messages, "run_command")
        assert "built ok" in output
        attempts.append("verified")
        return text("Fixed the missing build script and verified the build runs.")

    agent = make_agent(PolicyProvider(policy))
    result = await agent.run("run the build")

    assert result.ok
    assert attempts == ["run-missing-script", "diagnose", "fix", "retry", "verified"]
    assert result.failed_tool_calls == 1  # only the first attempt failed


@pytest.mark.asyncio
async def test_tool_errors_are_visible_to_the_model_with_classification(make_agent):
    seen: dict[str, str] = {}

    def policy(messages, tools, index):
        if index == 0:
            return calls(call("read_file", path="does-not-exist.txt"))
        seen["output"] = last_tool_output(messages, "read_file")
        return text("noted")

    agent = make_agent(PolicyProvider(policy))
    await agent.run("read a missing file")
    assert "ERROR [not_found]" in seen["output"]
    assert "HINT:" in seen["output"]


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_not_fatal(make_agent):
    def policy(messages, tools, index):
        if index == 0:
            return calls(call("teleport", destination="mars"))
        output = last_tool_output(messages)
        assert "unknown tool" in output
        return text("recovered")

    agent = make_agent(PolicyProvider(policy))
    result = await agent.run("do something impossible")
    assert result.ok
    assert result.failed_tool_calls == 1


@pytest.mark.asyncio
async def test_malformed_tool_arguments_become_recoverable_errors(make_agent):
    bad = ToolCall(name="read_file", arguments={}, parse_error="model emitted invalid JSON")

    def policy(messages, tools, index):
        if index == 0:
            from lema.providers.base import ChatResponse

            return ChatResponse(tool_calls=[bad], finish_reason="tool_calls")
        output = last_tool_output(messages, "read_file")
        assert "invalid JSON" in output
        return text("fixed my call")

    agent = make_agent(PolicyProvider(policy))
    result = await agent.run("read a file")
    assert result.ok


@pytest.mark.asyncio
async def test_iteration_limit_is_enforced_and_configurable(make_agent):
    provider = ScriptedProvider(
        [calls(call("list_directory", path="."))], loop_last=True
    )
    agent = make_agent(provider, max_iterations=5)
    result = await agent.run("loop forever")
    assert result.stop_reason == STOP_MAX_ITERATIONS
    assert result.iterations == 5
    assert "iteration limit" in result.content


@pytest.mark.asyncio
async def test_default_iteration_limit_is_generous(config):
    assert config.agent.max_iterations >= 50


@pytest.mark.asyncio
async def test_repeated_identical_calls_trigger_a_nudge(make_agent, events):
    provider = ScriptedProvider(
        [calls(call("read_file", path="missing.txt"))], loop_last=True
    )
    agent = make_agent(provider, max_iterations=6)
    agent.on_event = events
    await agent.run("read a file that is not there")

    warnings = [e for e in events.collected if e.type is EventType.WARNING]
    assert any("identical arguments" in e.text for e in warnings)
    # The nudge is also injected into the conversation.
    assert any("identical arguments" in m.content for m in agent.context.messages)


@pytest.mark.asyncio
async def test_retryable_provider_errors_are_retried(make_agent):
    provider = FailingProvider(
        ProviderConnectionError("connection reset"), failures=2, then=text("ok after retry")
    )
    agent = make_agent(provider)
    # Speed up the backoff.
    original = asyncio.sleep

    result = await agent.run("try me")
    assert result.ok
    assert result.content == "ok after retry"
    assert provider.attempts == 3


@pytest.mark.asyncio
async def test_non_retryable_provider_error_stops_the_turn(make_agent, config):
    config.agent.max_consecutive_errors = 2
    provider = FailingProvider(ProviderError("bad request", retryable=False), failures=99)
    agent = make_agent(provider)
    result = await agent.run("try me")
    assert result.stop_reason == STOP_FATAL
    assert "bad request" in (result.error or "")


@pytest.mark.asyncio
async def test_context_overflow_triggers_compaction_and_retry(make_agent, config):
    """A provider that rejects the first request forces a compaction."""
    config.context.keep_recent_messages = 2

    class OverflowThenOk(ScriptedProvider):
        def __init__(self):
            super().__init__([text("recovered after compaction")])
            self.overflowed = False

        async def _respond(self, messages, tools):
            if not self.overflowed:
                self.overflowed = True
                raise ContextOverflowError("prompt is too long")
            return await super()._respond(messages, tools)

    provider = OverflowThenOk()
    agent = make_agent(provider)
    # Pre-fill history so there is something to compact.
    for i in range(10):
        agent.context.add(Message.user(f"old message {i}"))
        agent.context.add(Message.assistant(f"old reply {i}"))

    result = await agent.run("continue")
    assert result.ok
    assert provider.overflowed
    assert agent.context.compactions == 1


@pytest.mark.asyncio
async def test_interrupt_stops_the_loop_and_leaves_a_valid_transcript(make_agent):
    provider = ScriptedProvider([calls(call("read_file", path="x"))], loop_last=True)
    agent = make_agent(provider, max_iterations=50)

    # Interrupt deterministically, right after the first tool finishes.
    async def on_event(event):
        if event.type is EventType.TOOL_END:
            agent.interrupt()

    agent.on_event = on_event
    result = await agent.run("long task")

    assert result.stop_reason == STOP_INTERRUPTED
    # Every assistant tool call must have a matching tool result, or the next
    # provider request would be rejected.
    answered = {
        m.tool_call_id for m in agent.context.messages if m.role is Role.TOOL
    }
    for message in agent.context.messages:
        for tc in message.tool_calls:
            assert tc.id in answered


@pytest.mark.asyncio
async def test_empty_model_response_is_nudged_once(make_agent):
    provider = ScriptedProvider([text(""), text("here is the real answer")])
    agent = make_agent(provider)
    result = await agent.run("answer me")
    assert result.ok
    assert result.content == "here is the real answer"
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_tool_call_burst_is_capped(make_agent, config):
    config.agent.max_tool_calls_per_turn = 2
    provider = ScriptedProvider(
        [
            calls(
                call("list_directory", path="."),
                call("list_directory", path="."),
                call("list_directory", path="."),
            ),
            text("done"),
        ]
    )
    agent = make_agent(provider)
    result = await agent.run("burst")
    assert result.ok
    overflow = [
        m for m in agent.context.messages if m.role is Role.TOOL and "not executed" in m.content
    ]
    assert len(overflow) == 1


@pytest.mark.asyncio
async def test_events_describe_the_whole_turn(make_agent, events):
    provider = ScriptedProvider(
        [calls(call("write_file", path="e.txt", content="x")), text("done")]
    )
    agent = make_agent(provider, on_event=events)
    await agent.run("write a file")

    types = [e.type for e in events.collected]
    assert EventType.TURN_START in types
    assert EventType.TOOL_START in types
    assert EventType.TOOL_END in types
    assert EventType.TURN_END in types

    tool_end = next(e for e in events.collected if e.type is EventType.TOOL_END)
    assert tool_end.result is not None and tool_end.result.ok
    assert tool_end.tool_name == "write_file"


@pytest.mark.asyncio
async def test_conversation_is_preserved_between_turns(make_agent):
    provider = ScriptedProvider([text("first"), text("second")])
    agent = make_agent(provider)
    await agent.run("turn one")
    await agent.run("turn two")
    contents = [m.content for m in agent.context.messages]
    assert "turn one" in contents
    assert "first" in contents
    assert "turn two" in contents
    # The second request carried the first turn's history.
    assert any(m.content == "first" for m in provider.requests[1])


@pytest.mark.asyncio
async def test_system_prompt_contains_environment_and_skills(make_agent, skills_with_builtin, config):
    from lema.agent.context import ContextManager

    context = ContextManager(config, skills_with_builtin, cwd=config.workspace)
    provider = ScriptedProvider([text("ok")])
    agent = Agent(
        config,
        provider,
        __import__("lema.tools", fromlist=["x"]).build_default_registry(include_web=False),
        context,
        __import__("lema.tools.base", fromlist=["ToolContext"]).ToolContext(
            config=config,
            cwd=config.workspace,
            project_root=config.project_root,
            permissions=__import__(
                "lema.agent.permissions", fromlist=["PermissionManager"]
            ).PermissionManager(mode=config.permissions),
            skills=skills_with_builtin,
        ),
    )
    await agent.run("what can you do?")
    prompt = provider.system_prompts()[0]
    assert "Working directory" in prompt
    assert str(config.workspace) in prompt
    assert "# Available skills" in prompt
    assert "debugging" in prompt
    assert "create_skill" in prompt  # the skills doctrine
