"""Skill registry: discovery, hot loading, creation, evolution.

The critical property of this module is that :meth:`SkillRegistry.create` and
:meth:`SkillRegistry.update` make a skill usable **immediately**, in the same
process and the same agent turn.  Writing the file and re-indexing happen in
one step, and ``revision`` is bumped so the agent knows to refresh the skill
material it injects into the next model request.

Nothing is ever destroyed silently: every update and delete snapshots the
previous content into ``.versions/`` next to the skill.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from lema.skills.model import (
    SKILL_FILENAME,
    Skill,
    SkillScope,
    SkillValidationError,
    extract_keywords,
    normalize_name,
    parse_skill,
    utc_now,
    validate_skill_content,
)

VERSIONS_DIRNAME = ".versions"


@dataclass
class SkillSource:
    """One directory scanned for skills."""

    path: Path
    scope: SkillScope
    writable: bool = True


@dataclass
class SkillChange:
    """Result of a mutating registry operation."""

    action: str  # created | updated | deleted | restored
    skill: Skill | None
    path: Path
    version: int
    warnings: list[str] = field(default_factory=list)
    previous_version_path: Path | None = None


class SkillRegistry:
    """In-memory index over one or more skill directories."""

    def __init__(self, sources: Sequence[SkillSource] | None = None):
        self._sources: list[SkillSource] = list(sources or [])
        self._skills: dict[str, Skill] = {}
        #: Incremented on every mutation so callers can detect staleness.
        self.revision: int = 0
        self.load_errors: list[str] = []

    # ---------------------------------------------------------- inspection

    @property
    def sources(self) -> list[SkillSource]:
        return list(self._sources)

    def add_source(self, source: SkillSource, *, reload: bool = True) -> None:
        if any(s.path == source.path for s in self._sources):
            return
        self._sources.append(source)
        if reload:
            self.reload()

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self) -> list[str]:
        return sorted(self._skills)

    def all(self) -> list[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str) -> Skill | None:
        if not name:
            return None
        try:
            key = normalize_name(name)
        except SkillValidationError:
            return None
        skill = self._skills.get(key)
        if skill is not None:
            return skill
        # Tolerate near-misses such as "godot export" vs "godot-export".
        for candidate, value in self._skills.items():
            if candidate.replace("-", "") == key.replace("-", ""):
                return value
        return None

    def suggest(self, name: str, limit: int = 3) -> list[str]:
        import difflib

        return difflib.get_close_matches(name, self.names(), n=limit, cutoff=0.4)

    def index_text(self, limit: int | None = None) -> str:
        """Compact catalogue injected into the system prompt."""
        skills = self.all()
        if limit is not None:
            skills = skills[:limit]
        if not skills:
            return "(no skills available yet - create one with create_skill when you learn a reusable procedure)"
        return "\n".join(skill.index_entry() for skill in skills)

    # ------------------------------------------------------------ loading

    def reload(self) -> int:
        """Rescan every source directory. Returns the number of skills loaded.

        Later sources win on name collisions, so a project skill shadows a
        global skill, which shadows a built-in one.
        """
        discovered: dict[str, Skill] = {}
        self.load_errors = []

        for source in sorted(self._sources, key=lambda s: s.scope.priority):
            if not source.path.exists():
                continue
            for path in self._iter_skill_files(source.path):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    self.load_errors.append(f"{path}: {exc}")
                    continue
                try:
                    skill = parse_skill(text, path=path, scope=source.scope)
                except SkillValidationError as exc:
                    self.load_errors.append(f"{path}: {exc}")
                    continue
                existing = discovered.get(skill.name)
                if existing is None or skill.scope.priority >= existing.scope.priority:
                    discovered[skill.name] = skill

        self._skills = discovered
        self.revision += 1
        return len(self._skills)

    @staticmethod
    def _iter_skill_files(root: Path) -> Iterable[Path]:
        """Find ``<name>/SKILL.md`` and flat ``<name>.md`` files."""
        if not root.is_dir():
            return []
        found: list[Path] = []
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") or entry.name == VERSIONS_DIRNAME:
                continue
            if entry.is_dir():
                skill_file = entry / SKILL_FILENAME
                if skill_file.is_file():
                    found.append(skill_file)
                    continue
                # Nested category directories, e.g. skills/lang/python/SKILL.md
                for nested in sorted(entry.iterdir()):
                    if nested.is_dir() and (nested / SKILL_FILENAME).is_file():
                        found.append(nested / SKILL_FILENAME)
            elif entry.suffix.lower() == ".md" and entry.name.lower() != "readme.md":
                found.append(entry)
        return found

    # ----------------------------------------------------------- mutation

    def _writable_source(self, scope: SkillScope | str | None) -> SkillSource:
        if scope is None:
            # Prefer project scope, then global.
            for wanted in (SkillScope.PROJECT, SkillScope.GLOBAL, SkillScope.EXTRA):
                for source in self._sources:
                    if source.scope is wanted and source.writable:
                        return source
            raise SkillValidationError("no writable skill directory is configured")

        wanted_scope = SkillScope(scope) if not isinstance(scope, SkillScope) else scope
        for source in self._sources:
            if source.scope is wanted_scope and source.writable:
                return source
        raise SkillValidationError(
            f"no writable skill directory for scope {wanted_scope.value!r}; "
            f"available: {', '.join(s.scope.value for s in self._sources if s.writable) or 'none'}"
        )

    @staticmethod
    def _skill_path(source: SkillSource, name: str) -> Path:
        return source.path / name / SKILL_FILENAME

    def _snapshot(self, skill: Skill) -> Path | None:
        """Copy the current content into ``.versions/`` before overwriting."""
        if skill.path is None or not skill.path.exists():
            return None
        versions = skill.path.parent / VERSIONS_DIRNAME
        versions.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().replace(":", "").replace("-", "")
        target = versions / f"v{skill.version}-{stamp}.md"
        shutil.copy2(skill.path, target)
        return target

    def create(
        self,
        name: str,
        content: str,
        *,
        description: str | None = None,
        keywords: Sequence[str] | None = None,
        scope: SkillScope | str | None = None,
        overwrite: bool = False,
    ) -> SkillChange:
        """Create a skill and register it immediately (no restart required)."""
        key = normalize_name(name)
        warnings = validate_skill_content(content, name=key)

        existing = self._skills.get(key)
        if existing is not None and not overwrite:
            raise SkillValidationError(
                f"skill {key!r} already exists (scope: {existing.scope.value}, "
                f"v{existing.version}). Use update_skill to improve it, or pass "
                f"overwrite=true to replace it."
            )

        source = self._writable_source(scope)
        path = self._skill_path(source, key)
        path.parent.mkdir(parents=True, exist_ok=True)

        previous_path = None
        version = 1
        if existing is not None:
            previous_path = self._snapshot(existing)
            version = existing.version + 1

        skill = parse_skill(content, name=key, path=path, scope=source.scope)
        skill.version = version
        if description:
            skill.description = description.strip()
        if keywords:
            skill.keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        elif not skill.keywords:
            skill.keywords = extract_keywords(key, skill.content)
        now = utc_now()
        skill.created_at = existing.created_at if existing and existing.created_at else now
        skill.updated_at = now

        path.write_text(skill.to_markdown(), encoding="utf-8")

        # Register in memory right away - this is what makes the new skill
        # usable within the current session.
        self._skills[key] = skill
        self.revision += 1

        return SkillChange(
            action="updated" if existing is not None else "created",
            skill=skill,
            path=path,
            version=version,
            warnings=warnings,
            previous_version_path=previous_path,
        )

    def update(
        self,
        name: str,
        *,
        content: str | None = None,
        append: str | None = None,
        description: str | None = None,
        keywords: Sequence[str] | None = None,
    ) -> SkillChange:
        """Improve an existing skill, preserving the previous version."""
        skill = self.get(name)
        if skill is None:
            suggestions = self.suggest(name)
            extra = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            raise SkillValidationError(f"no skill named {name!r}.{extra}")

        if content is None and append is None and description is None and keywords is None:
            raise SkillValidationError(
                "update_skill needs content, append, description or keywords"
            )

        new_content = skill.content
        if content is not None:
            new_content = content
        if append is not None:
            new_content = new_content.rstrip() + "\n\n" + append.strip() + "\n"

        warnings = validate_skill_content(new_content, name=skill.name)

        if skill.scope is SkillScope.BUILTIN:
            # Never mutate the packaged copy: fork it into a writable scope so
            # the user's install stays pristine and the change is visible.
            source = self._writable_source(None)
            path = self._skill_path(source, skill.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            previous_path = None
            new_scope = source.scope
            warnings.append(
                f"built-in skill forked into {new_scope.value} scope at {path}"
            )
        else:
            path = skill.path or self._skill_path(self._writable_source(skill.scope), skill.name)
            previous_path = self._snapshot(skill)
            new_scope = skill.scope

        updated = parse_skill(new_content, name=skill.name, path=path, scope=new_scope)
        updated.version = skill.version + 1
        updated.description = (description or updated.description or skill.description).strip()
        if keywords:
            updated.keywords = [str(k).strip().lower() for k in keywords if str(k).strip()]
        elif not updated.keywords:
            updated.keywords = skill.keywords
        updated.created_at = skill.created_at or utc_now()
        updated.updated_at = utc_now()

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated.to_markdown(), encoding="utf-8")

        self._skills[updated.name] = updated
        self.revision += 1

        return SkillChange(
            action="updated",
            skill=updated,
            path=path,
            version=updated.version,
            warnings=warnings,
            previous_version_path=previous_path,
        )

    def delete(self, name: str) -> SkillChange:
        """Remove a skill, keeping a snapshot so it can be recovered."""
        skill = self.get(name)
        if skill is None:
            raise SkillValidationError(f"no skill named {name!r}")
        if skill.scope is SkillScope.BUILTIN:
            raise SkillValidationError(
                f"{skill.name!r} is a built-in skill and cannot be deleted. "
                "Override it by creating a project or global skill with the same name."
            )
        if skill.path is None or not skill.path.exists():
            raise SkillValidationError(f"skill {skill.name!r} has no file on disk")

        snapshot = self._snapshot(skill)
        skill.path.unlink()
        # Remove the skill directory when only the versions folder remains.
        parent = skill.path.parent
        if parent.name != VERSIONS_DIRNAME:
            remaining = [p for p in parent.iterdir() if p.name != VERSIONS_DIRNAME]
            if not remaining and parent.name == skill.name:
                pass  # keep .versions/ so the deletion is recoverable

        self._skills.pop(skill.name, None)
        self.revision += 1
        return SkillChange(
            action="deleted",
            skill=skill,
            path=skill.path,
            version=skill.version,
            previous_version_path=snapshot,
        )

    def history(self, name: str) -> list[Path]:
        """Previous versions of a skill, newest first."""
        skill = self.get(name)
        if skill is None or skill.path is None:
            return []
        versions = skill.path.parent / VERSIONS_DIRNAME
        if not versions.is_dir():
            return []
        return sorted(versions.glob("*.md"), reverse=True)


def default_sources(
    project_root: Path,
    *,
    include_builtin: bool = True,
    extra_dirs: Sequence[str | Path] = (),
) -> list[SkillSource]:
    """Standard skill search path, lowest priority first."""
    from lema.util import paths

    sources: list[SkillSource] = []
    if include_builtin:
        builtin = Path(__file__).resolve().parent / "builtin"
        sources.append(SkillSource(path=builtin, scope=SkillScope.BUILTIN, writable=False))
    for extra in extra_dirs:
        sources.append(
            SkillSource(path=Path(str(extra)).expanduser(), scope=SkillScope.EXTRA, writable=True)
        )
    sources.append(SkillSource(path=paths.global_skills_dir(), scope=SkillScope.GLOBAL))
    sources.append(
        SkillSource(path=paths.project_skills_dir(project_root), scope=SkillScope.PROJECT)
    )
    return sources


def build_registry(
    project_root: Path,
    *,
    include_builtin: bool = True,
    extra_dirs: Sequence[str | Path] = (),
) -> SkillRegistry:
    registry = SkillRegistry(
        default_sources(project_root, include_builtin=include_builtin, extra_dirs=extra_dirs)
    )
    registry.reload()
    return registry
