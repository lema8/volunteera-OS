"""Context assembly, skill retrieval and compaction."""

from __future__ import annotations

import pytest

from lema.agent.compaction import compact
from lema.agent.context import ContextManager
from lema.agent.prompts import COMPACTION_PROMPT
from lema.providers.base import Message, ProviderError, Role, ToolCall
from lema.skills.retrieval import KeywordRetriever, get_retriever, register_retriever, select_skills
from lema.tools.base import ToolResult
from lema.util.tokens import estimate_tokens, truncate_middle
from scripted import ScriptedProvider, text


def make_context(config, skills, **kwargs):
    for key, value in kwargs.items():
        setattr(config.context, key, value)
    return ContextManager(config, skills, cwd=config.workspace)


# ------------------------------------------------------------ system prompt


def test_system_prompt_states_the_environment(config, skills):
    context = make_context(config, skills)
    prompt = context.build_system_message("do something").content
    assert str(config.workspace) in prompt
    assert "Linux" in prompt


def test_system_prompt_lists_the_skill_index_not_every_body(config, skills_with_builtin):
    """Spec §7: index always, full bodies only when relevant."""
    context = make_context(config, skills_with_builtin, )
    config.skills.max_injected = 0
    prompt = context.build_system_message("hello").content
    assert "debugging" in prompt  # the index entry is present
    body = skills_with_builtin.get("debugging").content
    assert body not in prompt  # ...but not the whole skill


def test_relevant_skills_are_injected_in_full(config, skills_with_builtin):
    config.skills.max_injected = 2
    context = make_context(config, skills_with_builtin)
    context.build_system_message("the pytest suite is failing, please debug it")
    assert context.injected_skills
    assert set(context.injected_skills) & {"debugging", "testing"}


def test_irrelevant_skills_are_not_injected(config, skills_with_builtin):
    config.skills.max_injected = 2
    context = make_context(config, skills_with_builtin)
    context.build_system_message("what is the capital of France?")
    assert "git-workflow" not in context.injected_skills


def test_skill_injection_is_capped(config, skills_with_builtin):
    config.skills.max_injected = 1
    context = make_context(config, skills_with_builtin)
    context.build_system_message("debug the failing git test build project shell")
    assert len(context.injected_skills) <= 1


def test_a_new_skill_invalidates_the_cached_prompt(config, skills):
    """This is what makes a skill created mid-session visible immediately."""
    context = make_context(config, skills)
    first = context.build_system_message("do a widget export")
    assert "widget-export" not in first.content

    skills.create(
        "widget-export",
        "# Widget Export\n\n## When to use\nWhen the user asks to export widgets.\n\n"
        "## Procedure\n1. Run `make widgets`.\n2. Check `dist/widgets.json` exists.\n"
        "3. Verify the row count matches the source data.\n",
        description="Export widgets from the build system",
    )
    assert context.skills_changed()
    second = context.build_system_message("do a widget export")
    assert "widget-export" in second.content


def test_project_information_is_included(config, skills, workspace):
    (workspace / "package.json").write_text('{"name": "demo"}')
    (workspace / "Makefile").write_text("build:\n\techo hi\n")
    context = make_context(config, skills)
    context.refresh_project()
    prompt = context.build_system_message("build the project").content
    assert "package.json" in prompt
    assert "Makefile" in prompt


def test_git_summary_is_included_when_set(config, skills):
    context = make_context(config, skills)
    context.set_git_summary("branch: feature/login (3 modified)")
    assert "feature/login" in context.build_system_message("x").content


def test_custom_instructions_are_appended(config, skills):
    config.agent.system_prompt_append = "Always answer in haiku."
    context = make_context(config, skills)
    assert "Always answer in haiku." in context.build_system_message("x").content


def test_agents_md_is_picked_up(config, skills, workspace):
    (workspace / "AGENTS.md").write_text("# House rules\n\nUse tabs, not spaces.")
    context = make_context(config, skills)
    context.refresh_project()
    assert "Use tabs, not spaces." in context.build_system_message("write code").content


def test_session_notices_reach_the_model(config, skills):
    context = make_context(config, skills)
    context.add_notice("the user switched the model to gpt-4o")
    assert "switched the model" in context.build_system_message("x").content


# ------------------------------------------------------------------ history


def test_messages_round_trip(config, skills):
    context = make_context(config, skills)
    context.add_user("hello")
    context.add(Message.assistant("hi"))
    assert [m.role for m in context.messages] == [Role.USER, Role.ASSISTANT]
    request = context.build_request("hello")
    assert request[0].role is Role.SYSTEM
    assert request[-1].content == "hi"


def test_tool_results_are_truncated_but_flagged():
    result = ToolResult.success("x" * 50000)
    rendered = result.render_for_model(2000)
    assert len(rendered) < 3000
    assert "truncated" in rendered.lower()


def test_truncate_middle_keeps_both_ends():
    text_value = "START" + ("m" * 5000) + "END"
    out = truncate_middle(text_value, 200)
    assert out.startswith("START")
    assert out.endswith("END")
    assert len(out) < 400


def test_token_estimate_tracks_growth(config, skills):
    context = make_context(config, skills)
    before = context.estimate_tokens()
    for _ in range(20):
        context.add_user("a fairly long message " * 20)
    assert context.estimate_tokens() > before


def test_estimate_tokens_is_monotonic():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("a" * 1000) > estimate_tokens("a" * 100)


def test_touched_files_are_tracked(config, skills):
    context = make_context(config, skills)
    context.add(Message.tool("wrote it", "1", "write_file", touched=["src/app.py"]))
    assert any("app.py" in f for f in context.touched_files())


def test_clear_keeps_the_session_alive(config, skills):
    context = make_context(config, skills)
    context.add_user("one")
    context.clear()
    assert context.messages == []
    assert context.build_system_message("x").content


def test_stats_report_usage(config, skills):
    context = make_context(config, skills)
    context.add_user("hello")
    stats = context.stats()
    assert stats.estimated_tokens > 0
    assert stats.messages == 1
    assert 0 <= stats.utilization <= 1
    assert "context" in stats.render()


# --------------------------------------------------------------- compaction


def test_compaction_triggers_at_the_threshold(config, skills):
    config.provider.context_window = 32000
    context = make_context(config, skills, compaction_threshold=0.5)
    assert not context.needs_compaction()
    for _ in range(400):
        context.add_user("filler text that consumes context " * 10)
    assert context.needs_compaction()
    assert context.stats().utilization >= 0.5


def test_auto_compact_can_be_disabled(config, skills):
    config.provider.context_window = 500
    context = make_context(config, skills, compaction_threshold=0.1, auto_compact=False)
    for _ in range(50):
        context.add_user("filler " * 50)
    assert not context.needs_compaction()


@pytest.mark.asyncio
async def test_compaction_preserves_the_required_facts(config, skills):
    """Spec §16: requirements, decisions, files, changes, problems, tests."""
    messages = [
        Message.user("Add OAuth login to the API. It must support Google."),
        Message.assistant("Plan: use authlib, edit api/auth.py"),
        Message.user("go ahead"),
    ]
    messages += [Message.assistant(f"step {i}") for i in range(20)]

    summary_text = (
        "## Requirements\n- OAuth login with Google\n"
        "## Files\n- api/auth.py\n"
        "## Decisions\n- use authlib\n"
        "## Open problems\n- tests not written\n"
    )
    provider = ScriptedProvider([text(summary_text)])
    provider.auto_summarize = False

    new_messages, result = await compact(messages, provider, keep_recent=2)

    assert result.removed_messages > 0
    assert len(new_messages) < len(messages)
    joined = "\n".join(m.content for m in new_messages)
    assert "OAuth" in joined
    assert "api/auth.py" in joined
    assert "step 19" in joined  # recent turns survive verbatim
    assert not result.fallback
    # The summariser was given the compaction instructions.
    assert provider.requests[0][0].role is Role.SYSTEM
    assert "compacting" in provider.requests[0][0].content.lower()


@pytest.mark.asyncio
async def test_compaction_never_orphans_a_tool_result(config, skills):
    """Cutting between an assistant tool call and its result breaks the API."""
    messages: list[Message] = []
    for i in range(10):
        messages.append(
            Message.assistant("", [ToolCall(name="read_file", arguments={}, id=f"c{i}")])
        )
        messages.append(Message.tool(f"result {i}", f"c{i}", "read_file"))

    provider = ScriptedProvider([text("summary")])
    provider.auto_summarize = False
    new_messages, _ = await compact(messages, provider, keep_recent=3)

    answered = {m.tool_call_id for m in new_messages if m.role is Role.TOOL}
    for message in new_messages:
        for tool_call in message.tool_calls:
            assert tool_call.id in answered, "assistant tool call lost its result"
    call_ids = {tc.id for m in new_messages for tc in m.tool_calls}
    for message in new_messages:
        if message.role is Role.TOOL:
            assert message.tool_call_id in call_ids, "orphan tool result"


@pytest.mark.parametrize("keep_recent", [1, 2, 3, 4, 5, 8])
@pytest.mark.asyncio
async def test_compaction_boundary_is_safe_at_every_cut_point(keep_recent):
    messages: list[Message] = [Message.user("start")]
    for i in range(6):
        messages.append(Message.assistant("thinking"))
        messages.append(
            Message.assistant("", [ToolCall(name="t", arguments={}, id=f"x{i}")])
        )
        messages.append(Message.tool("out", f"x{i}", "t"))

    provider = ScriptedProvider([text("summary")])
    provider.auto_summarize = False
    new_messages, _ = await compact(messages, provider, keep_recent=keep_recent)

    call_ids = {tc.id for m in new_messages for tc in m.tool_calls}
    answered = {m.tool_call_id for m in new_messages if m.role is Role.TOOL}
    assert call_ids == answered


@pytest.mark.asyncio
async def test_compaction_falls_back_when_the_provider_fails():
    messages = [Message.user(f"message {i}") for i in range(30)]

    class Broken(ScriptedProvider):
        auto_summarize = False

        async def _respond(self, messages, tools):
            raise ProviderError("summarisation failed")

    new_messages, result = await compact(messages, Broken([text("unused")]), keep_recent=2)
    assert result.fallback
    assert result.summary  # a deterministic local summary, not nothing
    assert len(new_messages) < len(messages)


@pytest.mark.asyncio
async def test_compaction_is_a_no_op_on_a_short_history():
    messages = [Message.user("hi")]
    provider = ScriptedProvider([text("summary")])
    provider.auto_summarize = False
    new_messages, result = await compact(messages, provider, keep_recent=12)
    assert result.removed_messages == 0
    assert new_messages == messages
    assert provider.call_count == 0


@pytest.mark.asyncio
async def test_compaction_actually_saves_tokens():
    messages = [Message.user("a long rambling message " * 100) for _ in range(30)]
    provider = ScriptedProvider([text("short summary")])
    provider.auto_summarize = False
    _, result = await compact(messages, provider, keep_recent=3)
    assert result.tokens_after < result.tokens_before
    assert result.saved > 0
    assert "Compacted" in result.render()


def test_compaction_prompt_names_what_to_keep():
    """Spec §16: the summary must retain the state needed to continue."""
    lowered = COMPACTION_PROMPT.lower()
    for section in ("## task", "## decisions", "## files", "## changes",
                    "## commands and results", "## outstanding"):
        assert section in lowered
    assert "failing tests" in lowered
    assert "next step" in lowered


# ---------------------------------------------------------------- retrieval


def test_keyword_retriever_ranks_by_relevance(skills_with_builtin):
    retriever = KeywordRetriever()
    ranked = retriever.retrieve(
        "my pytest run is failing with an assertion error", skills_with_builtin, limit=3
    )
    assert ranked
    names = [scored.skill.name for scored in ranked]
    assert {"testing", "debugging"} & set(names)
    # Scores are ordered, best first.
    assert ranked == sorted(ranked, key=lambda s: -s.score)


def test_retrieval_returns_nothing_for_unrelated_text(skills_with_builtin):
    chosen = select_skills(
        "what is the weather in Paris", skills_with_builtin, limit=3, threshold=0.5
    )
    assert chosen == []


def test_retrieval_honours_the_threshold(skills_with_builtin):
    loose = select_skills("git commit branch", skills_with_builtin, limit=5, threshold=0.0)
    strict = select_skills("git commit branch", skills_with_builtin, limit=5, threshold=0.95)
    assert len(loose) >= len(strict)


def test_explicit_skill_name_ranks_first(skills_with_builtin):
    chosen = select_skills(
        "follow the git-workflow skill please", skills_with_builtin, limit=1, threshold=0.0
    )
    assert chosen and chosen[0].name == "git-workflow"


def test_always_include_forces_a_skill(skills_with_builtin):
    chosen = select_skills(
        "totally unrelated query",
        skills_with_builtin,
        limit=2,
        threshold=0.9,
        always_include=["testing"],
    )
    assert [s.name for s in chosen] == ["testing"]


def test_retrieval_interface_is_pluggable(skills_with_builtin):
    """Spec §7: keyword now, semantic later, behind one interface."""
    from lema.skills.retrieval import ScoredSkill, SkillRetriever

    class AlwaysFirst(SkillRetriever):
        name = "always-first"

        def retrieve(self, query, registry, *, limit=4, threshold=0.0):
            return [ScoredSkill(skill=registry.all()[0], score=1.0, reasons=["stub"])]

    register_retriever("always-first", AlwaysFirst)
    retriever = get_retriever("always-first")
    assert isinstance(retriever, AlwaysFirst)
    ranked = retriever.retrieve("anything at all", skills_with_builtin)
    assert len(ranked) == 1 and ranked[0].score == 1.0

    chosen = select_skills("anything", skills_with_builtin, strategy="always-first")
    assert len(chosen) == 1


def test_unknown_strategy_falls_back_to_keyword():
    assert isinstance(get_retriever("semantic-not-installed-yet"), KeywordRetriever)
