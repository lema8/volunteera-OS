"""Conversation and context management.

Responsibilities:

* own the message list and keep an accurate token estimate
* rebuild the system prompt when the environment changes (a new skill is
  created, the branch changes, the user switches directory)
* select which skills to inject for the current task
* decide when compaction is needed

Retrieval is targeted: we never dump the repository into the prompt.  The
agent pulls what it needs with tools, and the context manager only maintains
the ambient state (project shape, git status, relevant skills).
"""

from __future__ import annotations

import platform as platform_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from lema.agent.project import ProjectInfo, detect_project
from lema.agent.prompts import build_system_prompt
from lema.config.schema import Config
from lema.providers.base import Message, Role
from lema.skills.model import Skill
from lema.skills.registry import SkillRegistry
from lema.skills.retrieval import select_skills
from lema.util.tokens import estimate_obj_tokens, estimate_tokens


@dataclass
class ContextStats:
    messages: int
    estimated_tokens: int
    context_window: int
    system_tokens: int
    utilization: float
    compaction_threshold: float
    needs_compaction: bool
    compactions: int

    def render(self) -> str:
        bar_width = 28
        filled = min(bar_width, int(self.utilization * bar_width))
        bar = "█" * filled + "░" * (bar_width - filled)
        return (
            f"context  {bar} {self.utilization * 100:5.1f}%\n"
            f"  messages:        {self.messages}\n"
            f"  est. tokens:     {self.estimated_tokens:,} / {self.context_window:,}\n"
            f"  system prompt:   {self.system_tokens:,} tokens\n"
            f"  compact at:      {self.compaction_threshold * 100:.0f}%"
            + ("  (DUE)" if self.needs_compaction else "")
            + f"\n  compactions:     {self.compactions}"
        )


class ContextManager:
    """Owns the message history and everything injected around it."""

    def __init__(
        self,
        config: Config,
        skills: SkillRegistry,
        *,
        cwd: Path | None = None,
    ):
        self.config = config
        self.skills = skills
        self.cwd = Path(cwd or config.workspace)
        self.messages: list[Message] = []
        self.compactions = 0

        self._system_prompt: str = ""
        self._system_tokens: int = 0
        self._skills_revision: int = -1
        self._injected_skills: list[str] = []
        self._git_summary: str = ""
        self._project: ProjectInfo | None = None
        self._last_task: str = ""
        #: Notes appended to the system prompt (e.g. "skill X was just created").
        self._notices: list[str] = []

    # ------------------------------------------------------------ project

    @property
    def project(self) -> ProjectInfo:
        if self._project is None:
            self._project = detect_project(self.config.project_root)
        return self._project

    def refresh_project(self) -> ProjectInfo:
        self._project = detect_project(self.config.project_root)
        self._system_prompt = ""
        return self._project

    def set_git_summary(self, summary: str) -> None:
        if summary != self._git_summary:
            self._git_summary = summary
            self._system_prompt = ""  # force rebuild

    def set_cwd(self, cwd: Path) -> None:
        self.cwd = Path(cwd)
        self._system_prompt = ""

    def add_notice(self, text: str) -> None:
        """Surface a session event (like a new skill) in the next prompt."""
        if text not in self._notices:
            self._notices.append(text)
            self._system_prompt = ""

    # ------------------------------------------------------------- skills

    def select_task_skills(self, task: str) -> list[Skill]:
        cfg = self.config.skills
        if cfg.max_injected <= 0:
            return []
        return select_skills(
            task,
            self.skills,
            limit=cfg.max_injected,
            threshold=cfg.relevance_threshold,
            strategy=cfg.retrieval,
        )

    @property
    def injected_skills(self) -> list[str]:
        return list(self._injected_skills)

    def skills_changed(self) -> bool:
        return self.skills.revision != self._skills_revision

    # ------------------------------------------------------ system prompt

    def build_system_message(self, task: str | None = None) -> Message:
        """Build (or reuse) the system message for the next request."""
        if task:
            self._last_task = task

        # Rebuild when the skill registry changed - this is what makes a
        # freshly created skill visible to the very next model call.
        if self.skills_changed():
            self._system_prompt = ""
            self._skills_revision = self.skills.revision

        if self._system_prompt:
            return Message.system(self._system_prompt, role_kind="system")

        cfg = self.config
        selected = self.select_task_skills(self._last_task) if self._last_task else []
        self._injected_skills = [s.name for s in selected]
        bodies = "\n\n".join(s.render() for s in selected)

        index = ""
        if cfg.skills.inject_index:
            index = self.skills.index_text()

        project_summary = self.project.summary() if cfg.context.include_project_info else ""
        instructions = self.project.instructions if cfg.context.include_project_info else ""

        extra = ""
        if self._notices:
            extra = "# Session notes\n" + "\n".join(f"- {n}" for n in self._notices[-10:])

        self._system_prompt = build_system_prompt(
            cfg,
            cwd=str(self.cwd),
            project_summary=project_summary,
            git_summary=self._git_summary if cfg.context.include_git_status else "",
            skill_index=index,
            skill_bodies=bodies,
            project_instructions=instructions,
            platform=f"{platform_module.system()} {platform_module.release()}",
            extra=extra,
        )
        self._system_tokens = estimate_tokens(self._system_prompt)
        return Message.system(self._system_prompt)

    def invalidate_system_prompt(self) -> None:
        self._system_prompt = ""

    # ----------------------------------------------------------- messages

    def add(self, message: Message) -> None:
        self.messages.append(message)

    def extend(self, messages: Iterable[Message]) -> None:
        self.messages.extend(messages)

    def add_user(self, content: str) -> Message:
        message = Message.user(content)
        self._last_task = content
        # A new task changes which skills are relevant.
        self.invalidate_system_prompt()
        self.messages.append(message)
        return message

    def build_request(self, task: str | None = None) -> list[Message]:
        """The full message list to send to the provider."""
        return [self.build_system_message(task), *self.messages]

    def clear(self, *, keep_system: bool = True) -> None:
        self.messages = []
        self.compactions = 0
        self._notices = []
        if not keep_system:
            self._system_prompt = ""

    # -------------------------------------------------------------- stats

    def estimate_tokens(self) -> int:
        total = self._system_tokens or estimate_tokens(self._system_prompt)
        for message in self.messages:
            total += estimate_tokens(message.content) + 4
            for call in message.tool_calls:
                total += estimate_obj_tokens(call.arguments) + estimate_tokens(call.name) + 8
        return total

    def stats(self) -> ContextStats:
        if not self._system_prompt:
            self.build_system_message()
        window = max(1, self.config.provider.context_window)
        used = self.estimate_tokens()
        threshold = self.config.context.compaction_threshold
        return ContextStats(
            messages=len(self.messages),
            estimated_tokens=used,
            context_window=window,
            system_tokens=self._system_tokens,
            utilization=used / window,
            compaction_threshold=threshold,
            needs_compaction=used / window >= threshold,
            compactions=self.compactions,
        )

    def needs_compaction(self) -> bool:
        if not self.config.context.auto_compact:
            return False
        return self.stats().needs_compaction

    # ------------------------------------------------------------ helpers

    def recent_tool_results(self, limit: int = 5) -> list[Message]:
        out = [m for m in self.messages if m.role is Role.TOOL]
        return out[-limit:]

    def touched_files(self) -> list[str]:
        files: list[str] = []
        for message in self.messages:
            for path in message.metadata.get("touched", []) or []:
                if path not in files:
                    files.append(path)
        return files

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "compactions": self.compactions,
            "injected_skills": self._injected_skills,
        }
