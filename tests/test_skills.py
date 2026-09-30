"""Skill model, registry and retrieval tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lema.skills.model import (
    SkillScope,
    SkillValidationError,
    extract_keywords,
    normalize_name,
    parse_skill,
    validate_skill_content,
)
from lema.skills.registry import SkillRegistry, SkillSource, build_registry
from lema.skills.retrieval import KeywordRetriever, select_skills

GOOD = """\
# Docker Compose Debugging

## Purpose

Diagnose Docker Compose startup and networking problems.

## When to use

Use this skill when containers fail to start or services cannot reach each other.

## Procedure

1. Inspect the compose file.
2. Check container status with `docker compose ps`.
3. Read logs with `docker compose logs -f <service>`.

## Common failures

Port conflicts and missing environment variables.

## Verification

`docker compose ps` shows every service as healthy.
"""


# ------------------------------------------------------------------ parsing


def test_normalize_name():
    assert normalize_name("Godot Export") == "godot-export"
    assert normalize_name("  python_packaging  ") == "python-packaging"
    assert normalize_name("A//B**C") == "abc"
    with pytest.raises(SkillValidationError):
        normalize_name("***")


def test_parse_skill_infers_everything_from_markdown():
    skill = parse_skill(GOOD, name="docker-compose-debugging")
    assert skill.name == "docker-compose-debugging"
    assert skill.title == "Docker Compose Debugging"
    assert "Diagnose Docker Compose" in skill.description
    assert "Procedure" in skill.sections
    assert skill.keywords
    assert "docker" in skill.keywords


def test_parse_skill_reads_frontmatter():
    text = (
        "---\n"
        "name: custom-name\n"
        "description: A custom description\n"
        "keywords: alpha, beta, gamma\n"
        "version: 4\n"
        "---\n\n" + GOOD
    )
    skill = parse_skill(text)
    assert skill.name == "custom-name"
    assert skill.description == "A custom description"
    assert skill.keywords == ["alpha", "beta", "gamma"]
    assert skill.version == 4


def test_roundtrip_through_markdown():
    skill = parse_skill(GOOD, name="x")
    skill.version = 7
    reparsed = parse_skill(skill.to_markdown())
    assert reparsed.name == "x"
    assert reparsed.version == 7
    assert reparsed.content.strip() == skill.content.strip()


def test_validation_rejects_empty_and_trivial_content():
    with pytest.raises(SkillValidationError):
        validate_skill_content("")
    with pytest.raises(SkillValidationError):
        validate_skill_content("do the thing")


def test_validation_warns_about_missing_sections():
    warnings = validate_skill_content(
        "# Title\n\nThis skill explains a procedure in prose but has no sections at all, "
        "which makes it much harder for an agent to follow reliably in practice."
    )
    assert any("Procedure" in w for w in warnings)
    assert any("When To Use" in w or "when to use" in w.lower() for w in warnings)


def test_extract_keywords_weights_the_name_and_headings():
    keywords = extract_keywords("docker-compose", GOOD)
    assert "docker" in keywords
    assert "compose" in keywords
    assert "the" not in keywords


# ----------------------------------------------------------------- registry


def test_create_registers_immediately(config, skills):
    assert skills.get("dcd") is None
    before = skills.revision
    change = skills.create("dcd", GOOD, scope=SkillScope.PROJECT)
    assert change.action == "created"
    assert change.version == 1
    assert skills.get("dcd") is not None
    assert skills.revision > before
    assert change.path.is_file()


def test_create_refuses_duplicates_unless_overwritten(skills):
    skills.create("dup", GOOD)
    with pytest.raises(SkillValidationError, match="already exists"):
        skills.create("dup", GOOD)
    change = skills.create("dup", GOOD.replace("Purpose", "Purpose v2"), overwrite=True)
    assert change.version == 2
    assert change.previous_version_path is not None


def test_update_preserves_history(skills):
    skills.create("evolving", GOOD)
    change = skills.update("evolving", append="## Extra\n\nMore detail about the procedure.")
    assert change.version == 2
    assert change.previous_version_path is not None
    assert change.previous_version_path.is_file()
    assert "Extra" not in change.previous_version_path.read_text()
    assert "Extra" in skills.get("evolving").content

    history = skills.history("evolving")
    assert len(history) == 1


def test_update_rejects_unknown_skill(skills):
    with pytest.raises(SkillValidationError, match="no skill named"):
        skills.update("nonexistent", append="x")


def test_delete_keeps_a_recoverable_snapshot(skills):
    skills.create("doomed", GOOD)
    change = skills.delete("doomed")
    assert skills.get("doomed") is None
    assert change.previous_version_path is not None
    assert change.previous_version_path.is_file()
    assert "Procedure" in change.previous_version_path.read_text()


def test_builtin_skills_cannot_be_deleted(config):
    registry = build_registry(config.project_root, include_builtin=True)
    assert registry.get("debugging") is not None
    with pytest.raises(SkillValidationError, match="built-in"):
        registry.delete("debugging")


def test_updating_a_builtin_forks_it_into_a_writable_scope(config):
    registry = build_registry(config.project_root, include_builtin=True)
    original = registry.get("debugging")
    assert original.scope is SkillScope.BUILTIN
    original_path = original.path

    change = registry.update("debugging", append="## Local addition\n\nProject-specific note.")
    updated = registry.get("debugging")
    assert updated.scope in (SkillScope.PROJECT, SkillScope.GLOBAL)
    assert "Local addition" in updated.content
    # The packaged copy is untouched.
    assert "Local addition" not in original_path.read_text()
    assert any("forked" in w for w in change.warnings)


def test_project_skills_shadow_global_skills(tmp_path):
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    for directory, marker in ((global_dir, "GLOBAL VERSION"), (project_dir, "PROJECT VERSION")):
        skill_dir = directory / "shared"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(GOOD.replace("Diagnose", marker + " Diagnose"))

    registry = SkillRegistry(
        [
            SkillSource(global_dir, SkillScope.GLOBAL),
            SkillSource(project_dir, SkillScope.PROJECT),
        ]
    )
    registry.reload()
    skill = registry.get("shared")
    assert skill.scope is SkillScope.PROJECT
    assert "PROJECT VERSION" in skill.content


def test_reload_picks_up_external_edits(config, skills):
    skill_dir = config.project_root / ".lema" / "skills" / "external"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(GOOD)
    assert skills.get("external") is None
    skills.reload()
    assert skills.get("external") is not None


def test_flat_markdown_files_are_loaded(config, tmp_path):
    directory = tmp_path / "flat"
    directory.mkdir()
    (directory / "flat-skill.md").write_text(GOOD)
    registry = SkillRegistry([SkillSource(directory, SkillScope.EXTRA)])
    registry.reload()
    assert registry.get("flat-skill") is not None


def test_malformed_skills_are_reported_not_fatal(tmp_path):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "bad.md").write_text("")
    (directory / "good.md").write_text(GOOD)
    registry = SkillRegistry([SkillSource(directory, SkillScope.EXTRA)])
    count = registry.reload()
    assert count == 1
    assert registry.load_errors


def test_index_text_lists_every_skill(skills):
    skills.create("alpha-one", GOOD)
    skills.create("beta-two", GOOD.replace("Docker Compose", "Kubernetes"))
    index = skills.index_text()
    assert "alpha-one" in index
    assert "beta-two" in index


# ---------------------------------------------------------------- retrieval


@pytest.fixture
def populated(skills):
    skills.create(
        "docker-compose-debugging",
        GOOD,
        keywords=["docker", "compose", "container", "networking"],
    )
    skills.create(
        "python-packaging",
        GOOD.replace("Docker Compose Debugging", "Python Packaging").replace(
            "Diagnose Docker Compose startup and networking problems.",
            "Build and publish Python distributions with pyproject and uv.",
        ),
        keywords=["python", "packaging", "pyproject", "wheel", "uv"],
    )
    skills.create(
        "rust-cross-compile",
        GOOD.replace("Docker Compose Debugging", "Rust Cross Compilation").replace(
            "Diagnose Docker Compose startup and networking problems.",
            "Cross compile Rust binaries for other targets with cargo.",
        ),
        keywords=["rust", "cargo", "cross", "target", "compile"],
    )
    return skills


def test_keyword_retriever_finds_the_right_skill(populated):
    retriever = KeywordRetriever()
    results = retriever.retrieve("my docker containers cannot talk to each other", populated, limit=2)
    assert results
    assert results[0].skill.name == "docker-compose-debugging"


def test_retriever_distinguishes_ecosystems(populated):
    retriever = KeywordRetriever()
    python = retriever.retrieve("publish a wheel to pypi with uv", populated, limit=1)
    assert python[0].skill.name == "python-packaging"
    rust = retriever.retrieve("build a cargo binary for aarch64", populated, limit=1)
    assert rust[0].skill.name == "rust-cross-compile"


def test_retriever_returns_nothing_for_unrelated_queries(populated):
    retriever = KeywordRetriever()
    results = retriever.retrieve("xyzzy plugh frobnicate", populated, limit=3)
    assert results == []


def test_select_skills_respects_the_limit(populated):
    selected = select_skills("docker python rust", populated, limit=2, threshold=0.0)
    assert len(selected) <= 2


def test_exact_name_mention_wins(populated):
    selected = select_skills("use the rust-cross-compile skill", populated, limit=1, threshold=0.0)
    assert selected[0].name == "rust-cross-compile"
