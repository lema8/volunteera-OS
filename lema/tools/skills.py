"""Skill tools - the agent's self-extension mechanism.

``create_skill`` is the defining capability of Lema Harness: when the agent
meets a task it has no procedure for, it writes that procedure down, the
registry indexes it *in the same process*, and the skill body is returned in
the tool result so the model can act on it immediately - no restart, no
re-prompt, no second session.
"""

from __future__ import annotations

from typing import Any, Sequence

from lema.skills.model import Skill, SkillScope, SkillValidationError
from lema.skills.registry import SkillRegistry
from lema.skills.retrieval import get_retriever
from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)
from lema.util.tokens import truncate_middle

SKILL_TEMPLATE_HINT = """\
A good skill is a reusable procedure, not a record of one task. Structure it as:

# <Title>

## Purpose
What class of problem this solves.

## When to use
Concrete triggers - the situations in which this skill applies.

## Procedure
Numbered, concrete steps. Name the exact commands and files.

## Common failures
What usually goes wrong and how to tell.

## Verification
How to prove the work actually succeeded.

## Examples
A worked example.
"""


def _require_registry(ctx: ToolContext) -> SkillRegistry:
    if ctx.skills is None:
        raise ToolError(
            "the skill registry is not available in this context",
            FailureKind.CONFIGURATION,
        )
    return ctx.skills


def _note_skill_event(ctx: ToolContext, action: str, skill: Skill | None, name: str) -> None:
    """Record the event so the agent loop can refresh its injected skills."""
    events = ctx.scratch.setdefault("skill_events", [])
    events.append({"action": action, "name": skill.name if skill else name})


class ListSkillsTool(Tool):
    name = "list_skills"
    description = (
        "List the skills currently available, with scope and description. "
        "Call this when you are unsure whether a procedure for the task already exists. "
        "If nothing relevant exists, create one with create_skill."
    )
    category = ToolCategory.SKILL
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional task description; ranks skills by relevance instead of listing all.",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "builtin", "global", "project", "extra"],
                "description": "Filter by where the skill is stored.",
                "default": "all",
            },
            "detailed": {"type": "boolean", "description": "Include keywords and sections.", "default": False},
        },
    }

    async def run(
        self,
        ctx: ToolContext,
        query: str | None = None,
        scope: str = "all",
        detailed: bool = False,
    ) -> ToolResult:
        registry = _require_registry(ctx)
        skills = registry.all()

        if scope and scope != "all":
            skills = [s for s in skills if s.scope.value == scope]

        ranked: list[tuple[Skill, float]] = []
        if query:
            retriever = get_retriever(ctx.config.skills.retrieval)
            scored = retriever.retrieve(query, registry, limit=50, threshold=0.0)
            allowed = {s.name for s in skills}
            ranked = [(s.skill, s.score) for s in scored if s.skill.name in allowed]
            if not ranked:
                return ToolResult.success(
                    f"No skill matches {query!r}. Available skills: "
                    + (", ".join(s.name for s in skills) or "(none)")
                    + "\n\nIf you need a procedure for this task, create one with create_skill.",
                    data={"count": 0, "query": query, "available": [s.name for s in skills]},
                )
        else:
            ranked = [(s, 0.0) for s in skills]

        if not ranked:
            return ToolResult.success(
                "No skills available yet. Use create_skill to record a reusable procedure.",
                data={"count": 0},
            )

        lines = [f"{len(ranked)} skill(s):"]
        for skill, score in ranked:
            prefix = f"[{score:.2f}] " if query else ""
            lines.append(f"- {prefix}{skill.name} ({skill.scope.value}, v{skill.version}): {skill.description}")
            if detailed:
                if skill.keywords:
                    lines.append(f"    keywords: {', '.join(skill.keywords[:10])}")
                if skill.sections:
                    lines.append(f"    sections: {', '.join(skill.sections[:8])}")
        lines.append("\nUse read_skill to load the full procedure for any of these.")

        return ToolResult.success(
            "\n".join(lines),
            data={
                "count": len(ranked),
                "skills": [s.to_dict() for s, _ in ranked],
                "revision": registry.revision,
            },
        )


class ReadSkillTool(Tool):
    name = "read_skill"
    description = (
        "Read the full text of a skill so you can follow its procedure. "
        "Use this after list_skills identifies a relevant skill."
    )
    category = ToolCategory.SKILL
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name, e.g. 'docker-compose-debugging'."},
        },
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, name: str) -> ToolResult:
        registry = _require_registry(ctx)
        skill = registry.get(name)
        if skill is None:
            suggestions = registry.suggest(name)
            hint = (
                f"Did you mean: {', '.join(suggestions)}?"
                if suggestions
                else "Use list_skills to see what exists, or create_skill to add this procedure."
            )
            raise ToolError(f"no skill named {name!r}", FailureKind.NOT_FOUND, hint=hint)

        header = (
            f"SKILL {skill.name} (scope: {skill.scope.value}, version: {skill.version}, "
            f"updated: {skill.updated_at or 'unknown'})"
        )
        return ToolResult.success(
            f"{header}\n\n{skill.content}",
            data=skill.to_dict(),
        )


class CreateSkillTool(Tool):
    name = "create_skill"
    description = (
        "Record a reusable procedure as a new skill. Use this when you have worked out how to "
        "do something that no existing skill covers - the skill becomes available immediately, "
        "in this same session, and persists for future sessions.\n\n"
        "Write generalised procedural knowledge, not a log of what you just did. "
        "Include: Purpose, When to use, Procedure (numbered, with exact commands), "
        "Common failures, Verification, Examples."
    )
    category = ToolCategory.SKILL
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short kebab-case identifier, e.g. 'godot-export' or 'python-uv-packaging'.",
            },
            "content": {
                "type": "string",
                "description": (
                    "The full skill in Markdown. Start with '# Title' and use the sections "
                    "Purpose / When to use / Procedure / Common failures / Verification / Examples."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-line summary used in the skill index. Inferred if omitted.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Retrieval keywords. Inferred from the content if omitted.",
            },
            "scope": {
                "type": "string",
                "enum": ["project", "global"],
                "description": (
                    "'project' stores it in ./.lema/skills (specific to this codebase), "
                    "'global' in ~/.config/lema/skills (available everywhere)."
                ),
                "default": "project",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing skill of the same name (the old version is kept in .versions/).",
                "default": False,
            },
        },
        "required": ["name", "content"],
    }

    def summarize_call(self, arguments: dict[str, Any]) -> str:
        return f"create_skill {arguments.get('name', '?')}"

    async def run(
        self,
        ctx: ToolContext,
        name: str,
        content: str,
        description: str | None = None,
        keywords: Sequence[str] | None = None,
        scope: str = "project",
        overwrite: bool = False,
    ) -> ToolResult:
        registry = _require_registry(ctx)
        try:
            change = registry.create(
                name,
                content,
                description=description,
                keywords=list(keywords) if keywords else None,
                scope=SkillScope(scope),
                overwrite=overwrite,
            )
        except SkillValidationError as exc:
            raise ToolError(
                str(exc),
                FailureKind.INVALID_INPUT,
                hint=SKILL_TEMPLATE_HINT,
            ) from exc

        skill = change.skill
        assert skill is not None
        _note_skill_event(ctx, change.action, skill, name)

        if ctx.logger is not None:
            ctx.logger.log_skill_event(change.action, skill.name, skill.version, str(change.path))

        # Verify it really is loaded - never claim success without checking.
        loaded = registry.get(skill.name)
        if loaded is None:  # pragma: no cover - defensive
            return ToolResult.failure(
                f"skill {skill.name!r} was written to {change.path} but did not register",
                FailureKind.UNKNOWN,
                hint="Call list_skills to inspect the registry state.",
            )

        lines = [
            f"Skill '{skill.name}' {change.action} and loaded (version {change.version}, scope {skill.scope.value}).",
            f"Stored at: {change.path}",
            f"Indexed keywords: {', '.join(skill.keywords[:10]) or '(none)'}",
            "",
            "It is ACTIVE NOW - no restart needed. Follow the procedure below to complete the task:",
            "",
            truncate_middle(loaded.content, 12000, "skill body truncated"),
        ]
        if change.previous_version_path:
            lines.insert(2, f"Previous version preserved at: {change.previous_version_path}")
        if change.warnings:
            lines.insert(
                1, "Quality warnings: " + "; ".join(change.warnings)
            )

        return ToolResult.success(
            "\n".join(lines),
            data={
                "name": skill.name,
                "action": change.action,
                "version": change.version,
                "path": str(change.path),
                "scope": skill.scope.value,
                "warnings": change.warnings,
                "registry_revision": registry.revision,
                "available_now": True,
            },
            metadata={"skill_event": change.action, "skill_name": skill.name},
        )


class UpdateSkillTool(Tool):
    name = "update_skill"
    description = (
        "Improve an existing skill when you discover it is incomplete, outdated or wrong. "
        "Pass `content` to rewrite it, or `append` to add a section. The previous version is "
        "always preserved in .versions/ - nothing is destroyed. "
        "Built-in skills are forked into your writable scope rather than modified in place."
    )
    category = ToolCategory.SKILL
    mutating = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill to update."},
            "content": {"type": "string", "description": "Full replacement Markdown."},
            "append": {"type": "string", "description": "Markdown appended to the end of the skill."},
            "description": {"type": "string", "description": "New one-line summary."},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Replacement retrieval keywords.",
            },
        },
        "required": ["name"],
    }

    async def run(
        self,
        ctx: ToolContext,
        name: str,
        content: str | None = None,
        append: str | None = None,
        description: str | None = None,
        keywords: Sequence[str] | None = None,
    ) -> ToolResult:
        registry = _require_registry(ctx)
        try:
            change = registry.update(
                name,
                content=content,
                append=append,
                description=description,
                keywords=list(keywords) if keywords else None,
            )
        except SkillValidationError as exc:
            raise ToolError(str(exc), FailureKind.INVALID_INPUT, hint=SKILL_TEMPLATE_HINT) from exc

        skill = change.skill
        assert skill is not None
        _note_skill_event(ctx, "updated", skill, name)
        if ctx.logger is not None:
            ctx.logger.log_skill_event("updated", skill.name, skill.version, str(change.path))

        lines = [
            f"Skill '{skill.name}' updated to version {change.version} and reloaded.",
            f"Stored at: {change.path}",
        ]
        if change.previous_version_path:
            lines.append(f"Previous version preserved at: {change.previous_version_path}")
        if change.warnings:
            lines.append("Warnings: " + "; ".join(change.warnings))
        lines.append("")
        lines.append("Current content:")
        lines.append(truncate_middle(skill.content, 10000, "skill body truncated"))

        return ToolResult.success(
            "\n".join(lines),
            data={
                "name": skill.name,
                "version": change.version,
                "path": str(change.path),
                "previous_version": str(change.previous_version_path)
                if change.previous_version_path
                else None,
                "registry_revision": registry.revision,
            },
            metadata={"skill_event": "updated", "skill_name": skill.name},
        )


class DeleteSkillTool(Tool):
    name = "delete_skill"
    description = (
        "Delete a skill. A snapshot is kept in .versions/ so it can be recovered. "
        "Built-in skills cannot be deleted, only overridden. Only do this when the user asks."
    )
    category = ToolCategory.SKILL
    mutating = True
    dangerous = True
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill to delete."}},
        "required": ["name"],
    }

    async def run(self, ctx: ToolContext, name: str) -> ToolResult:
        registry = _require_registry(ctx)
        try:
            change = registry.delete(name)
        except SkillValidationError as exc:
            raise ToolError(str(exc), FailureKind.INVALID_INPUT) from exc
        _note_skill_event(ctx, "deleted", change.skill, name)
        if ctx.logger is not None:
            ctx.logger.log_skill_event("deleted", name, change.version, str(change.path))
        return ToolResult.success(
            f"Skill '{name}' deleted. Snapshot kept at {change.previous_version_path}.",
            data={
                "name": name,
                "snapshot": str(change.previous_version_path)
                if change.previous_version_path
                else None,
                "registry_revision": registry.revision,
            },
            metadata={"skill_event": "deleted", "skill_name": name},
        )


class ReloadSkillsTool(Tool):
    name = "reload_skills"
    description = (
        "Rescan the skill directories from disk. Use this if skills were added or edited "
        "outside the harness (for example by a git pull)."
    )
    category = ToolCategory.SKILL
    read_only = True
    parameters = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext) -> ToolResult:
        registry = _require_registry(ctx)
        count = registry.reload()
        _note_skill_event(ctx, "reloaded", None, "*")
        body = [f"Reloaded {count} skill(s) from {len(registry.sources)} source director(ies)."]
        if registry.load_errors:
            body.append("Load errors:")
            body += [f"  {err}" for err in registry.load_errors]
        body.append("")
        body.append(registry.index_text())
        return ToolResult.success(
            "\n".join(body),
            data={
                "count": count,
                "errors": registry.load_errors,
                "revision": registry.revision,
            },
        )


def build_skill_tools() -> list[Tool]:
    return [
        ListSkillsTool(),
        ReadSkillTool(),
        CreateSkillTool(),
        UpdateSkillTool(),
        DeleteSkillTool(),
        ReloadSkillsTool(),
    ]
