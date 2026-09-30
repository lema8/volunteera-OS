"""Skill document model, parser and validator.

A skill is a Markdown file with optional front matter:

    ---
    name: docker-compose-debugging
    description: Diagnose Docker Compose startup and networking problems
    keywords: docker, compose, container, networking
    version: 2
    ---

    # Docker Compose Debugging

    ## Purpose
    ...

Front matter is optional: everything can be inferred from the Markdown so a
model writing plain Markdown still produces a valid skill.  The format is
deliberately plain text so it diffs cleanly in Git and a human can edit it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

#: Sections we encourage; absence is a warning, never an error.
RECOMMENDED_SECTIONS = ("purpose", "when to use", "procedure")

SKILL_FILENAME = "SKILL.md"

#: Very common English words, dropped from the keyword index.
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "when", "use", "using",
    "how", "what", "you", "your", "are", "was", "will", "can", "should", "have",
    "has", "not", "but", "all", "any", "its", "it's", "into", "onto", "out",
    "then", "than", "them", "they", "their", "there", "here", "which", "who",
    "why", "does", "did", "done", "get", "got", "set", "run", "make", "made",
    "used", "also", "some", "more", "most", "such", "each", "other", "about",
    "after", "before", "between", "while", "these", "those", "only", "just",
    "very", "one", "two", "new", "via", "per", "may", "might", "must", "need",
    "needs", "want", "like", "well", "way", "ways", "skill", "skills",
}


class SkillScope(str, Enum):
    BUILTIN = "builtin"   # ships with Lema
    GLOBAL = "global"     # ~/.config/lema/skills
    PROJECT = "project"   # ./.lema/skills
    EXTRA = "extra"       # configured extra_dirs

    @property
    def priority(self) -> int:
        """Higher wins when the same skill name exists in several scopes."""
        return {"builtin": 0, "extra": 1, "global": 2, "project": 3}[self.value]


class SkillValidationError(ValueError):
    """Raised when skill content cannot be accepted."""


@dataclass
class Skill:
    name: str
    title: str
    description: str
    content: str
    path: Path | None = None
    scope: SkillScope = SkillScope.PROJECT
    keywords: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    sections: list[str] = field(default_factory=list)

    # --------------------------------------------------------------- views

    def summary_line(self) -> str:
        return f"{self.name} [{self.scope.value}] - {self.description}"

    def index_entry(self) -> str:
        """Compact form used in the skill index injected into the prompt."""
        keywords = ", ".join(self.keywords[:8])
        suffix = f" (keywords: {keywords})" if keywords else ""
        return f"- {self.name}: {self.description}{suffix}"

    def render(self) -> str:
        """Full skill text as injected into the model context."""
        header = f"### SKILL: {self.name} (scope: {self.scope.value}, v{self.version})"
        return f"{header}\n{self.content.strip()}"

    def search_text(self) -> str:
        return " ".join(
            [self.name.replace("-", " "), self.title, self.description, " ".join(self.keywords)]
        ).lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "scope": self.scope.value,
            "keywords": self.keywords,
            "version": self.version,
            "path": str(self.path) if self.path else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sections": self.sections,
            "chars": len(self.content),
        }

    def to_markdown(self) -> str:
        """Serialise back to disk, front matter first."""
        return render_frontmatter(self) + self.content.strip() + "\n"


def normalize_name(raw: str) -> str:
    """Turn arbitrary text into a safe, stable skill directory name."""
    name = str(raw).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9\-.]", "", name)
    name = re.sub(r"-{2,}", "-", name).strip("-.")
    if not name:
        raise SkillValidationError(
            "skill name must contain at least one alphanumeric character"
        )
    if len(name) > 64:
        name = name[:64].rstrip("-")
    return name


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the simple ``key: value`` front matter block.

    We intentionally do not depend on a YAML parser: the subset used here is
    tiny, and avoiding the dependency keeps skills readable/writable by any
    model without it emitting YAML edge cases we would then have to handle.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace("-", "_")
        value = value.strip().strip("'\"")
        if key in {"keywords", "tags", "aliases"}:
            items = [v.strip().strip("'\"[]") for v in re.split(r"[,\n]", value)]
            meta[key] = [v for v in items if v]
        elif key == "version":
            try:
                meta[key] = int(float(value))
            except ValueError:
                meta[key] = 1
        else:
            meta[key] = value
    return meta, body


def render_frontmatter(skill: Skill) -> str:
    lines = ["---", f"name: {skill.name}"]
    if skill.description:
        lines.append(f"description: {skill.description}")
    if skill.keywords:
        lines.append("keywords: " + ", ".join(skill.keywords))
    lines.append(f"version: {skill.version}")
    if skill.created_at:
        lines.append(f"created_at: {skill.created_at}")
    if skill.updated_at:
        lines.append(f"updated_at: {skill.updated_at}")
    for key, value in skill.metadata.items():
        if key in {"name", "description", "keywords", "version", "created_at", "updated_at"}:
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def extract_keywords(name: str, text: str, limit: int = 24) -> list[str]:
    """Derive index keywords from the skill body.

    Weighted by where a term appears: the name and headings matter far more
    than prose, which keeps retrieval precise without any embedding model.
    """
    scores: dict[str, float] = {}

    def add(token: str, weight: float) -> None:
        token = token.strip().lower()
        if len(token) < 3 or token in STOPWORDS or token.isdigit():
            return
        scores[token] = scores.get(token, 0.0) + weight

    for part in re.split(r"[-_\s]+", name):
        add(part, 6.0)

    for heading in re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]{2,}", heading):
            add(token, 3.0)

    # Inline code and fenced command names are strong signals (tool names).
    for code in re.findall(r"`([^`\n]{2,40})`", text):
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#_-]{1,}", code)[:3]:
            add(token, 2.0)

    for token in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]{2,}", text):
        add(token, 0.15)

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [token for token, _ in ordered[:limit]]


def name_from_path(path: Path) -> str:
    """The skill name implied by a file's location.

    ``skills/godot-export/SKILL.md`` -> ``godot-export``
    ``skills/godot-export.md``       -> ``godot-export``
    """
    return path.parent.name if path.name == SKILL_FILENAME else path.stem


def parse_skill(
    text: str,
    *,
    name: str | None = None,
    path: Path | None = None,
    scope: SkillScope = SkillScope.PROJECT,
) -> Skill:
    """Parse skill Markdown into a :class:`Skill`.

    Name precedence: explicit argument > front matter > path > ``# Title``.
    The path outranks the title so that a hand-written skill is always
    addressable by the directory the user filed it under.
    """
    meta, body = _parse_frontmatter(text)
    body = body.strip()

    if not body:
        raise SkillValidationError(
            f"skill file is empty{f' ({path})' if path else ''}"
        )

    heading_match = HEADING_RE.search(body)
    title = str(meta.get("title") or (heading_match.group(1).strip() if heading_match else ""))

    resolved_name = name or meta.get("name")
    if not resolved_name and path is not None:
        resolved_name = name_from_path(path)
    if not resolved_name:
        resolved_name = title
    if not resolved_name:
        raise SkillValidationError("skill has no name (add front matter or a '# Title' heading)")
    resolved_name = normalize_name(str(resolved_name))

    if not title:
        title = resolved_name.replace("-", " ").title()

    description = str(meta.get("description") or "").strip()
    if not description:
        description = _infer_description(body, title)

    sections = [s.strip() for s in SECTION_RE.findall(body)]

    keywords = list(meta.get("keywords") or [])
    if not keywords:
        keywords = extract_keywords(resolved_name, body)
    keywords = [k.lower() for k in keywords if k]

    return Skill(
        name=resolved_name,
        title=title,
        description=description,
        content=body,
        path=path,
        scope=scope,
        keywords=keywords,
        version=int(meta.get("version") or 1),
        created_at=str(meta.get("created_at") or ""),
        updated_at=str(meta.get("updated_at") or ""),
        metadata={
            k: v
            for k, v in meta.items()
            if k not in {"name", "description", "keywords", "version", "created_at", "updated_at", "title"}
        },
        sections=sections,
    )


def _infer_description(body: str, title: str) -> str:
    """Use the Purpose section, or the first prose paragraph."""
    purpose = re.search(
        r"^##\s+Purpose\s*\n+(.+?)(?=\n#{1,2}\s|\Z)", body, re.MULTILINE | re.DOTALL | re.IGNORECASE
    )
    candidate = ""
    if purpose:
        candidate = purpose.group(1)
    else:
        stripped = HEADING_RE.sub("", body, count=1).strip()
        for block in stripped.split("\n\n"):
            block = block.strip()
            if block and not block.startswith(("#", "-", "*", "```", "|")):
                candidate = block
                break
    candidate = " ".join(candidate.split())
    if not candidate:
        return title
    if len(candidate) > 220:
        candidate = candidate[:217].rstrip() + "..."
    return candidate


def validate_skill_content(text: str, name: str | None = None) -> list[str]:
    """Validate skill text. Returns warnings; raises on hard errors."""
    if not text or not text.strip():
        raise SkillValidationError("skill content is empty")
    if len(text) > 400_000:
        raise SkillValidationError("skill content is unreasonably large (>400k characters)")

    skill = parse_skill(text, name=name)
    warnings: list[str] = []

    stripped = skill.content.strip()
    if len(stripped) < 80:
        raise SkillValidationError(
            "skill content is too short to be useful; describe the purpose, "
            "when to use it, and a concrete step-by-step procedure"
        )
    if not HEADING_RE.search(stripped):
        warnings.append("no top-level '# Title' heading")

    lowered_sections = {s.lower() for s in skill.sections}
    for recommended in RECOMMENDED_SECTIONS:
        if not any(recommended in section for section in lowered_sections):
            warnings.append(f"missing recommended section: '{recommended.title()}'")

    if not skill.description:
        warnings.append("no description could be inferred; add front matter or a Purpose section")

    return warnings


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
