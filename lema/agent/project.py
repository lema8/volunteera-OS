"""Project awareness.

Rather than scanning the repository (expensive, noisy, and it blows the
context window), we detect the handful of marker files that actually tell you
what a project *is* and how to build, test and run it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lema.tools.filesystem import NOISE_DIRS


@dataclass
class Marker:
    filename: str
    language: str
    ecosystem: str
    kind: str = "manifest"


MARKERS: list[Marker] = [
    Marker("package.json", "javascript", "npm"),
    Marker("pnpm-lock.yaml", "javascript", "pnpm", "lockfile"),
    Marker("yarn.lock", "javascript", "yarn", "lockfile"),
    Marker("bun.lockb", "javascript", "bun", "lockfile"),
    Marker("deno.json", "typescript", "deno"),
    Marker("tsconfig.json", "typescript", "typescript", "config"),
    Marker("pyproject.toml", "python", "python"),
    Marker("setup.py", "python", "setuptools"),
    Marker("requirements.txt", "python", "pip"),
    Marker("Pipfile", "python", "pipenv"),
    Marker("uv.lock", "python", "uv", "lockfile"),
    Marker("poetry.lock", "python", "poetry", "lockfile"),
    Marker("Cargo.toml", "rust", "cargo"),
    Marker("go.mod", "go", "go"),
    Marker("pom.xml", "java", "maven"),
    Marker("build.gradle", "java", "gradle"),
    Marker("build.gradle.kts", "kotlin", "gradle"),
    Marker("Gemfile", "ruby", "bundler"),
    Marker("composer.json", "php", "composer"),
    Marker("CMakeLists.txt", "cpp", "cmake"),
    Marker("meson.build", "c", "meson"),
    Marker("Makefile", "make", "make", "build"),
    Marker("justfile", "just", "just", "build"),
    Marker("Dockerfile", "docker", "docker", "container"),
    Marker("docker-compose.yml", "docker", "compose", "container"),
    Marker("docker-compose.yaml", "docker", "compose", "container"),
    Marker("compose.yaml", "docker", "compose", "container"),
    Marker("flake.nix", "nix", "nix", "config"),
    Marker("shell.nix", "nix", "nix", "config"),
    Marker("PKGBUILD", "shell", "arch", "packaging"),
    Marker("terraform.tf", "terraform", "terraform", "config"),
    Marker("project.godot", "gdscript", "godot"),
    Marker("pubspec.yaml", "dart", "flutter"),
    Marker("mix.exs", "elixir", "mix"),
]

TEST_DIR_NAMES = ("tests", "test", "spec", "__tests__")


@dataclass
class ProjectInfo:
    root: Path
    name: str = ""
    languages: list[str] = field(default_factory=list)
    ecosystems: list[str] = field(default_factory=list)
    markers: list[str] = field(default_factory=list)
    is_git_repo: bool = False
    #: Commands discovered from manifests, e.g. {"test": "pytest"}.
    commands: dict[str, str] = field(default_factory=dict)
    entry_points: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    #: Agent instructions found in the repo (AGENTS.md, CLAUDE.md, ...).
    instructions: str = ""

    def summary(self) -> str:
        """Compact description injected into the system prompt."""
        lines = [f"Project: {self.name or self.root.name}", f"Root: {self.root}"]
        if self.languages:
            lines.append(f"Languages: {', '.join(self.languages)}")
        if self.ecosystems:
            lines.append(f"Ecosystems: {', '.join(self.ecosystems)}")
        if self.markers:
            lines.append(f"Markers: {', '.join(self.markers[:12])}")
        if self.commands:
            rendered = "; ".join(f"{k}: {v}" for k, v in list(self.commands.items())[:8])
            lines.append(f"Known commands: {rendered}")
        if self.test_dirs:
            lines.append(f"Test directories: {', '.join(self.test_dirs)}")
        if self.entry_points:
            lines.append(f"Entry points: {', '.join(self.entry_points[:5])}")
        if self.top_level:
            lines.append(f"Top level: {', '.join(self.top_level[:25])}")
        lines.append(f"Git repository: {'yes' if self.is_git_repo else 'no'}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "name": self.name,
            "languages": self.languages,
            "ecosystems": self.ecosystems,
            "markers": self.markers,
            "commands": self.commands,
            "is_git_repo": self.is_git_repo,
            "test_dirs": self.test_dirs,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _detect_package_json(root: Path, info: ProjectInfo) -> None:
    data = _read_json(root / "package.json")
    if not data:
        return
    info.name = info.name or str(data.get("name") or "")
    scripts = data.get("scripts") or {}
    manager = "npm"
    if (root / "pnpm-lock.yaml").exists():
        manager = "pnpm"
    elif (root / "yarn.lock").exists():
        manager = "yarn"
    elif (root / "bun.lockb").exists():
        manager = "bun"
    for key in ("test", "build", "dev", "start", "lint", "format", "typecheck"):
        if key in scripts:
            info.commands.setdefault(key, f"{manager} run {key}")
    if "main" in data:
        info.entry_points.append(str(data["main"]))
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    for framework in ("react", "next", "vue", "svelte", "express", "fastify", "vite", "jest", "vitest"):
        if framework in deps:
            info.ecosystems.append(framework)


def _detect_pyproject(root: Path, info: ProjectInfo) -> None:
    text = _read_text(root / "pyproject.toml")
    if not text:
        return
    name = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    if name:
        info.name = info.name or name.group(1)
    if "[tool.poetry]" in text:
        info.ecosystems.append("poetry")
    if "[tool.pytest" in text or "pytest" in text:
        info.commands.setdefault("test", "pytest")
    if "[tool.ruff" in text:
        info.commands.setdefault("lint", "ruff check .")
    if "[tool.black" in text:
        info.commands.setdefault("format", "black .")
    if "[tool.mypy" in text:
        info.commands.setdefault("typecheck", "mypy .")
    scripts = re.search(r"\[project\.scripts\](.*?)(?=\n\[|\Z)", text, re.DOTALL)
    if scripts:
        for line in scripts.group(1).splitlines():
            if "=" in line:
                info.entry_points.append(line.split("=")[0].strip())
    if (root / "uv.lock").exists():
        info.ecosystems.append("uv")


def _detect_cargo(root: Path, info: ProjectInfo) -> None:
    text = _read_text(root / "Cargo.toml")
    if not text:
        return
    name = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    if name:
        info.name = info.name or name.group(1)
    info.commands.setdefault("build", "cargo build")
    info.commands.setdefault("test", "cargo test")
    info.commands.setdefault("lint", "cargo clippy")
    info.commands.setdefault("format", "cargo fmt")


def _detect_go(root: Path, info: ProjectInfo) -> None:
    text = _read_text(root / "go.mod")
    if not text:
        return
    module = re.search(r"(?m)^module\s+(\S+)", text)
    if module:
        info.name = info.name or module.group(1).rsplit("/", 1)[-1]
    info.commands.setdefault("build", "go build ./...")
    info.commands.setdefault("test", "go test ./...")


def _detect_make(root: Path, info: ProjectInfo) -> None:
    text = _read_text(root / "Makefile")
    if not text:
        return
    targets = re.findall(r"(?m)^([a-zA-Z][\w.-]*):(?!=)", text)
    for target in targets:
        if target in ("test", "build", "lint", "format", "install", "run", "check", "all"):
            info.commands.setdefault(target, f"make {target}")


def detect_project(root: Path, *, read_instructions: bool = True) -> ProjectInfo:
    """Inspect a directory and summarise what kind of project it is."""
    root = Path(root).resolve()
    info = ProjectInfo(root=root)

    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return info

    present = {entry.name for entry in entries}
    info.is_git_repo = (root / ".git").exists()

    for marker in MARKERS:
        if marker.filename in present:
            info.markers.append(marker.filename)
            if marker.language not in info.languages:
                info.languages.append(marker.language)
            if marker.ecosystem not in info.ecosystems:
                info.ecosystems.append(marker.ecosystem)

    info.top_level = [
        (entry.name + "/") if entry.is_dir() else entry.name
        for entry in entries
        if entry.name not in NOISE_DIRS and not entry.name.startswith(".")
    ][:40]

    for name in TEST_DIR_NAMES:
        if (root / name).is_dir():
            info.test_dirs.append(name)

    for doc in ("README.md", "README.rst", "README", "CONTRIBUTING.md", "docs"):
        if doc in present:
            info.doc_files.append(doc)

    _detect_package_json(root, info)
    _detect_pyproject(root, info)
    _detect_cargo(root, info)
    _detect_go(root, info)
    _detect_make(root, info)

    if "requirements.txt" in present:
        info.commands.setdefault("install", "pip install -r requirements.txt")
    if "docker-compose.yml" in present or "compose.yaml" in present:
        info.commands.setdefault("compose-up", "docker compose up -d")

    if not info.name:
        info.name = root.name

    if read_instructions:
        info.instructions = load_instructions(root)

    return info


#: Convention files that projects use to give agents standing instructions.
INSTRUCTION_FILES = (
    "AGENTS.md",
    "LEMA.md",
    ".lema/AGENTS.md",
    ".lema/instructions.md",
    "CLAUDE.md",
    ".cursorrules",
)


def load_instructions(root: Path, limit: int = 12000) -> str:
    """Read project-level standing instructions for the agent, if any."""
    chunks: list[str] = []
    for relative in INSTRUCTION_FILES:
        path = root / relative
        if path.is_file():
            text = _read_text(path, limit)
            if text.strip():
                chunks.append(f"--- {relative} ---\n{text.strip()}")
    return "\n\n".join(chunks)[:limit]
