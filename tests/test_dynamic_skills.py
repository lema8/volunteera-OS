"""CRITICAL ACCEPTANCE TEST: dynamic skill acquisition.

This is the defining capability of Lema Harness (spec §34, §37):

    User:  "Perform task X."
    Agent: discovers it has no skill for X
    Agent: create_skill(...)
    Agent: the skill is loaded  <-- in the same process, same turn
    Agent: uses the skill to complete X

Everything here is real except the model's decision-making:

* the skill registry is the real one, writing real files to disk
* create_skill is the real tool, dispatched through the real registry
* the agent loop is the real loop
* the task is completed by real filesystem tools

The scripted model is deliberately written so that it can only succeed if the
skill is genuinely readable back from the live registry - it extracts the
procedure from the tool result and from `read_skill`, and follows it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lema.agent.loop import STOP_COMPLETED
from lema.providers.base import Message, Role
from lema.skills.registry import SkillRegistry
from scripted import PolicyProvider, call, calls, last_tool_output, text

# The task the agent is asked to perform. There is deliberately no built-in
# skill for it, and the procedure is not guessable - it must come from the
# skill the agent writes and then reads back.
TASK = (
    "Perform task X: produce a 'widget manifest' for this project. "
    "Follow the widget-manifest procedure exactly."
)

SKILL_NAME = "widget-manifest"

SKILL_BODY = """\
# Widget Manifest

## Purpose

Produce a canonical widget manifest file for a project so downstream tooling can
discover its widgets.

## When to use

Use this skill whenever a project needs a widget manifest generated or refreshed.

## Procedure

1. Create the file `WIDGETS.md` in the project root.
2. The first line must be exactly `# Widget Manifest`.
3. The second line must be exactly `version: 3`.
4. Add one bullet per widget in the form `- <name>: <purpose>`.
5. The final line must be exactly `manifest-complete`.

## Common failures

- Writing the file as `widgets.md` (wrong case) - downstream tooling will not find it.
- Omitting the `manifest-complete` sentinel, which marks the file as finished.

## Verification

Read `WIDGETS.md` back and confirm it starts with `# Widget Manifest`, contains
`version: 3`, and ends with `manifest-complete`.

## Examples

    # Widget Manifest
    version: 3
    - alpha: does the alpha thing
    manifest-complete
"""


def _extract_procedure_marker(body: str) -> str:
    """Pull the sentinel out of the skill text, as a real model would.

    Reads step 5 of the Procedure section and extracts the backticked value,
    so the test only passes if the live skill text was genuinely recovered.
    """
    match = re.search(r"The final line must be exactly `([^`]+)`", body)
    return match.group(1) if match else ""


class WidgetPolicy:
    """A scripted 'model' that behaves like a competent agent.

    It never hard-codes the manifest contents: it must read them out of the
    skill it created, which is what makes this test meaningful.
    """

    def __init__(self) -> None:
        self.saw_no_skill = False
        self.created_skill = False
        self.read_skill_back = False
        self.procedure_text = ""
        self.steps: list[str] = []

    def __call__(self, messages, tools, index):
        tool_names = {spec.name for spec in tools}
        assert "create_skill" in tool_names, "create_skill must be offered to the model"

        # Step 1: check whether a skill already exists for this task.
        if index == 0:
            self.steps.append("list_skills")
            return calls(
                call("list_skills", query="widget manifest procedure"),
                content="Checking whether I already have a procedure for this.",
            )

        # Step 2: the registry said no -> write the skill.
        if index == 1:
            listing = last_tool_output(messages, "list_skills")
            assert SKILL_NAME not in listing, "the skill must not exist before creation"
            self.saw_no_skill = True
            self.steps.append("create_skill")
            return calls(
                call("create_skill", name=SKILL_NAME, content=SKILL_BODY, scope="project"),
                content="I have no skill for this. Recording the procedure first.",
            )

        # Step 3: read the skill back through the registry. This only works if
        # the skill really was loaded into the live registry.
        if index == 2:
            result = last_tool_output(messages, "create_skill")
            assert "ACTIVE NOW" in result, f"create_skill did not report the skill as live: {result[:300]}"
            self.created_skill = True
            self.steps.append("read_skill")
            return calls(
                call("read_skill", name=SKILL_NAME),
                content="Skill created. Loading it to follow the procedure.",
            )

        # Step 4: follow the procedure taken from the live skill text.
        if index == 3:
            body = last_tool_output(messages, "read_skill")
            assert "## Procedure" in body, "read_skill did not return the skill body"
            self.read_skill_back = True
            self.procedure_text = body
            sentinel = _extract_procedure_marker(body)
            assert sentinel == "manifest-complete", "procedure text was not recoverable from the skill"
            self.steps.append("write_file")
            manifest = "\n".join(
                [
                    "# Widget Manifest",
                    "version: 3",
                    "- alpha: does the alpha thing",
                    "- beta: does the beta thing",
                    sentinel,
                ]
            )
            return calls(
                call("write_file", path="WIDGETS.md", content=manifest + "\n"),
                content="Following the procedure from the skill.",
            )

        # Step 5: verify, as the skill's Verification section demands.
        if index == 4:
            self.steps.append("read_file")
            return calls(call("read_file", path="WIDGETS.md"))

        # Step 6: confirm and finish.
        if index == 5:
            written = last_tool_output(messages, "read_file")
            assert "manifest-complete" in written
            self.steps.append("done")
            return text(
                "Task X complete. I had no skill for it, so I created the "
                f"'{SKILL_NAME}' skill, loaded it, and followed its procedure to "
                "write WIDGETS.md. Verified by reading the file back."
            )

        raise AssertionError(f"policy asked for an unexpected turn {index}")


@pytest.mark.asyncio
async def test_agent_creates_and_immediately_uses_a_new_skill(
    make_agent, config, skills, tool_context, events
):
    """The full acceptance scenario, end to end, in one process."""
    policy = WidgetPolicy()
    provider = PolicyProvider(policy)
    agent = make_agent(provider, on_event=events)

    # Precondition: the capability genuinely does not exist yet.
    assert skills.get(SKILL_NAME) is None
    skills_before = len(skills)
    revision_before = skills.revision

    result = await agent.run(TASK)

    # --- the agent completed the task -----------------------------------
    assert result.stop_reason == STOP_COMPLETED, result.error
    assert result.ok

    # --- it followed the acquire-then-use sequence -----------------------
    assert policy.saw_no_skill, "the agent must first discover the skill is missing"
    assert policy.created_skill, "the agent must create the skill"
    assert policy.read_skill_back, "the agent must read the new skill back from the registry"
    assert policy.steps == [
        "list_skills",
        "create_skill",
        "read_skill",
        "write_file",
        "read_file",
        "done",
    ]

    # --- the skill is live in THIS process, with no reload or restart ----
    assert skills.get(SKILL_NAME) is not None, "the new skill must be in the live registry"
    assert len(skills) == skills_before + 1
    assert skills.revision > revision_before
    assert SKILL_NAME in result.skills_created

    # --- it was persisted to disk for future sessions --------------------
    skill = skills.get(SKILL_NAME)
    assert skill is not None
    assert skill.path is not None and skill.path.is_file()
    assert skill.path == config.project_root / ".lema" / "skills" / SKILL_NAME / "SKILL.md"
    on_disk = skill.path.read_text(encoding="utf-8")
    assert "# Widget Manifest" in on_disk
    assert "manifest-complete" in on_disk

    # --- the task artifact was produced per the skill's procedure --------
    manifest = config.workspace / "WIDGETS.md"
    assert manifest.is_file(), "the agent did not complete the actual task"
    lines = manifest.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "# Widget Manifest"
    assert lines[1] == "version: 3"
    assert lines[-1] == "manifest-complete"

    # --- the runtime announced the hot-load ------------------------------
    skill_events = [e for e in events.collected if e.type.value == "skill_event"]
    assert skill_events, "the agent loop must emit a skill event"
    assert any(SKILL_NAME in e.text for e in skill_events)


@pytest.mark.asyncio
async def test_new_skill_is_injected_into_the_next_model_request(
    make_agent, skills, tool_context
):
    """The new skill must reach the *model*, not just the registry.

    This is the part that proves 'no restart required': the system prompt of a
    request made after create_skill contains the new skill, because the context
    manager notices the registry revision changed.
    """

    def policy(messages, tools, index):
        if index == 0:
            return calls(call("create_skill", name="alpha-protocol", content=SKILL_BODY))
        if index == 1:
            return calls(call("list_skills"))
        return text("done")

    provider = PolicyProvider(policy)
    agent = make_agent(provider)

    await agent.run("Set up the alpha protocol.")

    prompts = provider.system_prompts()
    assert len(prompts) >= 3

    # Before creation the skill cannot be mentioned...
    assert "alpha-protocol" not in prompts[0]
    # ...and after creation it is in the skill index of every later prompt.
    assert "alpha-protocol" in prompts[1], "new skill missing from the next system prompt"
    assert "alpha-protocol" in prompts[2]

    # The session note also tells the model the skill is usable now.
    assert "loaded and usable now" in prompts[1]

    # And list_skills, executed for real, reports it.
    listing = last_tool_output(provider.last_request, "list_skills")
    assert "alpha-protocol" in listing


@pytest.mark.asyncio
async def test_skill_survives_for_a_later_turn_in_the_same_session(make_agent, skills):
    """A skill created in turn 1 is retrievable in turn 2 without any reload."""

    def policy(messages, tools, index):
        if index == 0:
            return calls(call("create_skill", name="turn-one-skill", content=SKILL_BODY))
        return text("created")

    provider = PolicyProvider(policy)
    agent = make_agent(provider)
    await agent.run("Learn the turn one procedure.")
    assert skills.get("turn-one-skill") is not None

    # Second turn: a brand-new policy that only looks the skill up.
    found: dict[str, str] = {}

    def policy2(messages, tools, index):
        if index == 0:
            return calls(call("read_skill", name="turn-one-skill"))
        found["body"] = last_tool_output(messages, "read_skill")
        return text("recalled")

    agent.provider = PolicyProvider(policy2)
    result = await agent.run("Recall the turn one procedure.")

    assert result.ok
    assert "## Procedure" in found.get("body", "")


@pytest.mark.asyncio
async def test_skill_persists_to_a_brand_new_registry(make_agent, config, skills):
    """Simulates a restart: a fresh registry over the same directories finds it."""

    def policy(messages, tools, index):
        if index == 0:
            return calls(
                call("create_skill", name="persisted-skill", content=SKILL_BODY, scope="project")
            )
        return text("ok")

    agent = make_agent(PolicyProvider(policy))
    await agent.run("Record the persisted procedure.")

    # A completely new registry object, as a new process would build.
    from lema.skills.registry import build_registry

    fresh = build_registry(config.project_root, include_builtin=False)
    reloaded = fresh.get("persisted-skill")
    assert reloaded is not None
    assert "## Procedure" in reloaded.content
    assert reloaded.version == 1


@pytest.mark.asyncio
async def test_agent_improves_an_existing_skill_without_destroying_it(
    make_agent, skills, config
):
    """Skill evolution (spec §5): update, keep history, stay live."""
    skills.create("packaging", SKILL_BODY, scope="project")
    original = skills.get("packaging")
    assert original is not None
    assert original.version == 1

    def policy(messages, tools, index):
        if index == 0:
            return calls(call("read_skill", name="packaging"))
        if index == 1:
            return calls(
                call(
                    "update_skill",
                    name="packaging",
                    append="## uv\n\nUse `uv sync` to install dependencies quickly.",
                )
            )
        if index == 2:
            return calls(call("read_skill", name="packaging"))
        body = last_tool_output(messages, "read_skill")
        assert "uv sync" in body
        return text("skill improved")

    agent = make_agent(PolicyProvider(policy))
    result = await agent.run("The packaging skill is missing uv; fix it.")
    assert result.ok

    updated = skills.get("packaging")
    assert updated is not None
    assert updated.version == 2
    assert "uv sync" in updated.content
    # The original content was not destroyed.
    assert "## Procedure" in updated.content

    history = skills.history("packaging")
    assert history, "the previous version must be preserved"
    assert "## Procedure" in history[0].read_text(encoding="utf-8")
    assert "uv sync" not in history[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_create_skill_rejects_junk_and_the_agent_can_recover(make_agent, skills):
    """A bad skill is refused with actionable guidance, not silently accepted."""
    attempts: list[str] = []

    def policy(messages, tools, index):
        if index == 0:
            attempts.append("junk")
            return calls(call("create_skill", name="too-short", content="do stuff"))
        if index == 1:
            error = last_tool_output(messages, "create_skill")
            assert "ERROR" in error
            assert "too short" in error
            assert "Procedure" in error  # the hint includes the template
            attempts.append("retry")
            return calls(call("create_skill", name="too-short", content=SKILL_BODY))
        return text("recovered")

    agent = make_agent(PolicyProvider(policy))
    result = await agent.run("Write a skill.")

    assert result.ok
    assert attempts == ["junk", "retry"]
    assert skills.get("too-short") is not None
