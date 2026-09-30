"""Search tools.

``ripgrep`` is used when present (it is fast and respects .gitignore); the
pure-Python fallback keeps the harness fully functional on machines without
it, which matters because the agent should never be blocked by a missing
optional dependency.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import shlex
from pathlib import Path
from typing import Any, Iterator

from lema.tools.base import (
    FailureKind,
    Tool,
    ToolCategory,
    ToolContext,
    ToolError,
    ToolResult,
)
from lema.tools.filesystem import NOISE_DIRS, is_probably_binary
from lema.tools.shell import run_shell
from lema.util.paths import shorten_path

#: Extension -> language, used by search_code to scope a search.
LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py", ".pyi"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx", ".mts", ".cts"],
    "rust": [".rs"],
    "go": [".go"],
    "java": [".java"],
    "kotlin": [".kt", ".kts"],
    "c": [".c", ".h"],
    "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".hh"],
    "csharp": [".cs"],
    "ruby": [".rb"],
    "php": [".php"],
    "swift": [".swift"],
    "shell": [".sh", ".bash", ".zsh"],
    "html": [".html", ".htm"],
    "css": [".css", ".scss", ".sass", ".less"],
    "sql": [".sql"],
    "markdown": [".md", ".mdx"],
    "yaml": [".yaml", ".yml"],
    "json": [".json"],
    "toml": [".toml"],
}

#: Language -> regex templates for symbol definitions.
_DEFINITION_PATTERNS: dict[str, list[str]] = {
    "python": [r"^\s*(?:async\s+)?def\s+{name}\b", r"^\s*class\s+{name}\b"],
    "javascript": [
        r"function\s+{name}\b",
        r"(?:const|let|var)\s+{name}\s*=",
        r"class\s+{name}\b",
        r"{name}\s*\(.*\)\s*\{{",
    ],
    "typescript": [
        r"function\s+{name}\b",
        r"(?:const|let|var)\s+{name}\s*[:=]",
        r"(?:class|interface|type|enum)\s+{name}\b",
    ],
    "rust": [r"fn\s+{name}\b", r"(?:struct|enum|trait|impl|type)\s+{name}\b"],
    "go": [r"func\s+(?:\([^)]*\)\s*)?{name}\b", r"type\s+{name}\b"],
    "java": [r"(?:class|interface|enum)\s+{name}\b", r"\w+\s+{name}\s*\("],
    "c": [r"\w+\s+\*?{name}\s*\(", r"(?:struct|enum|union)\s+{name}\b"],
    "cpp": [r"\w+\s+\*?{name}\s*\(", r"(?:class|struct|enum|namespace)\s+{name}\b"],
}


def has_ripgrep() -> bool:
    return shutil.which("rg") is not None


def _iter_files(
    root: Path,
    *,
    include_globs: list[str] | None = None,
    extensions: list[str] | None = None,
    max_files: int = 20000,
    show_hidden: bool = False,
) -> Iterator[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in NOISE_DIRS and (show_hidden or not d.startswith("."))
        ]
        for filename in filenames:
            if not show_hidden and filename.startswith("."):
                continue
            path = Path(dirpath) / filename
            if extensions and path.suffix.lower() not in extensions:
                continue
            if include_globs and not any(
                fnmatch.fnmatch(filename, g) or fnmatch.fnmatch(str(path), g)
                for g in include_globs
            ):
                continue
            yield path
            count += 1
            if count >= max_files:
                return


def resolve_case_sensitivity(pattern: str, case_sensitive: bool | None) -> bool:
    """Smart-case, the same rule ripgrep uses.

    ``None`` (the default) means: match case-insensitively unless the pattern
    itself contains an uppercase character. Searching for ``login`` finds
    ``Login``; searching for ``LOGIN`` does not find ``login``.
    """
    if case_sensitive is not None:
        return bool(case_sensitive)
    return any(ch.isupper() for ch in pattern)


async def _ripgrep_search(
    ctx: ToolContext,
    pattern: str,
    root: Path,
    *,
    globs: list[str] | None,
    case_sensitive: bool,
    regex: bool,
    max_results: int,
    context_lines: int,
) -> tuple[bool, list[str], int]:
    """Returns (used_rg, lines, match_count)."""
    if not has_ripgrep():
        return False, [], 0
    args = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "50"]
    if not case_sensitive:
        args.append("--ignore-case")
    if not regex:
        args.append("--fixed-strings")
    if context_lines > 0:
        args += ["--context", str(context_lines)]
    for glob in globs or []:
        args += ["--glob", glob]
    for noise in NOISE_DIRS:
        args += ["--glob", f"!{noise}/"]
    args += ["--", pattern, str(root)]

    command = " ".join(shlex.quote(a) for a in args)
    result = await run_shell(
        command, cwd=ctx.cwd, timeout=60, shell=ctx.config.tools.shell
    )
    # rg exits 1 when there are no matches - that is not an error.
    if result["exit_code"] not in (0, 1):
        return False, [], 0
    lines = [ln for ln in result["stdout"].splitlines() if ln.strip()]
    total = len(lines)
    return True, lines[:max_results], total


class SearchTextTool(Tool):
    name = "search_text"
    description = (
        "Search file contents for a string or regular expression (ripgrep when available). "
        "Returns file:line:match. Use this to find where something is defined or used."
    )
    category = ToolCategory.SEARCH
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Text or regular expression to find."},
            "path": {"type": "string", "description": "Directory or file to search.", "default": "."},
            "glob": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filename globs to include, e.g. ['*.py', '*.ts'].",
            },
            "regex": {"type": "boolean", "description": "Treat pattern as a regular expression.", "default": True},
            "case_sensitive": {
                "type": "boolean",
                "description": (
                    "Force case sensitivity. Omit for smart-case: insensitive "
                    "unless the pattern contains an uppercase letter."
                ),
            },
            "max_results": {"type": "integer", "description": "Maximum matching lines to return.", "default": 100},
            "context_lines": {"type": "integer", "description": "Lines of context around each match.", "default": 0},
        },
        "required": ["pattern"],
    }

    async def run(
        self,
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
        glob: list[str] | None = None,
        regex: bool = True,
        case_sensitive: bool | None = None,
        max_results: int = 100,
        context_lines: int = 0,
    ) -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        max_results = max(1, min(int(max_results), 1000))
        case_sensitive = resolve_case_sensitivity(pattern, case_sensitive)

        used_rg, lines, total = await _ripgrep_search(
            ctx,
            pattern,
            root,
            globs=glob,
            case_sensitive=case_sensitive,
            regex=regex,
            max_results=max_results,
            context_lines=int(context_lines),
        )

        if not used_rg:
            lines, total = self._python_search(
                pattern,
                root,
                globs=glob,
                regex=regex,
                case_sensitive=case_sensitive,
                max_results=max_results,
                context_lines=int(context_lines),
                cwd=ctx.cwd,
            )

        if not lines:
            return ToolResult.success(
                f"No matches for {pattern!r} in {shorten_path(root, ctx.cwd)}",
                data={"matches": 0, "pattern": pattern},
            )

        header = f"{total} match(es) for {pattern!r}"
        if total > len(lines):
            header += f" (showing first {len(lines)})"
        return ToolResult.success(
            header + "\n" + "\n".join(lines),
            data={"matches": total, "shown": len(lines), "pattern": pattern, "engine": "rg" if used_rg else "python"},
        )

    @staticmethod
    def _python_search(
        pattern: str,
        root: Path,
        *,
        globs: list[str] | None,
        regex: bool,
        case_sensitive: bool,
        max_results: int,
        context_lines: int,
        cwd: Path,
    ) -> tuple[list[str], int]:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern if regex else re.escape(pattern), flags)
        except re.error as exc:
            raise ToolError(
                f"invalid regular expression: {exc}",
                FailureKind.INVALID_INPUT,
                hint="Escape special characters or pass regex=false.",
            ) from exc

        out: list[str] = []
        total = 0
        targets = [root] if root.is_file() else _iter_files(root, include_globs=globs)
        for file_path in targets:
            if is_probably_binary(file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_lines = content.splitlines()
            for index, line in enumerate(file_lines):
                if not matcher.search(line):
                    continue
                total += 1
                if len(out) >= max_results:
                    continue
                rel = shorten_path(file_path, cwd)
                if context_lines > 0:
                    start = max(0, index - context_lines)
                    end = min(len(file_lines), index + context_lines + 1)
                    for ctx_index in range(start, end):
                        marker = ":" if ctx_index == index else "-"
                        out.append(f"{rel}{marker}{ctx_index + 1}{marker}{file_lines[ctx_index]}")
                    out.append("--")
                else:
                    out.append(f"{rel}:{index + 1}:{line}")
        return out, total


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Find files by name pattern (glob). Returns matching paths sorted by modification time. "
        "Use this to locate files before reading them."
    )
    category = ToolCategory.SEARCH
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob such as '*.py', 'test_*', or '**/config.*'."},
            "path": {"type": "string", "description": "Directory to search.", "default": "."},
            "max_results": {"type": "integer", "default": 200},
            "include_hidden": {"type": "boolean", "default": False},
        },
        "required": ["pattern"],
    }

    async def run(
        self,
        ctx: ToolContext,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        include_hidden: bool = False,
    ) -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        if not root.is_dir():
            raise ToolError(f"{root} is not a directory", FailureKind.INVALID_INPUT)

        # Normalise a bare name pattern into a recursive glob.
        globs = [pattern]
        if "/" not in pattern and not pattern.startswith("**"):
            globs.append(f"**/{pattern}")

        seen: dict[Path, float] = {}
        for candidate in _iter_files(root, show_hidden=include_hidden):
            rel = candidate.relative_to(root)
            if any(
                fnmatch.fnmatch(candidate.name, g)
                or fnmatch.fnmatch(str(rel), g)
                or fnmatch.fnmatch(str(rel), g.lstrip("*/"))
                for g in globs
            ):
                try:
                    seen[candidate] = candidate.stat().st_mtime
                except OSError:
                    seen[candidate] = 0.0

        ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
        total = len(ordered)
        shown = ordered[: max(1, int(max_results))]
        if not shown:
            return ToolResult.success(
                f"No files matching {pattern!r} under {shorten_path(root, ctx.cwd)}",
                data={"count": 0},
            )
        lines = [shorten_path(p, ctx.cwd) for p, _ in shown]
        header = f"{total} file(s) matching {pattern!r}"
        if total > len(shown):
            header += f" (showing {len(shown)})"
        return ToolResult.success(
            header + "\n" + "\n".join(lines),
            data={"count": total, "files": [str(p) for p, _ in shown]},
        )


class SearchCodeTool(Tool):
    name = "search_code"
    description = (
        "Find where a symbol (function, class, type, variable) is defined, optionally "
        "scoped to a language. Smarter than search_text for 'where does X come from?' questions."
    )
    category = ToolCategory.SEARCH
    read_only = True
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Name of the function/class/type to locate."},
            "path": {"type": "string", "description": "Directory to search.", "default": "."},
            "language": {
                "type": "string",
                "description": "Restrict to a language: " + ", ".join(sorted(LANGUAGE_EXTENSIONS)),
            },
            "include_references": {
                "type": "boolean",
                "description": "Also list usages, not just definitions.",
                "default": False,
            },
            "max_results": {"type": "integer", "default": 60},
        },
        "required": ["symbol"],
    }

    async def run(
        self,
        ctx: ToolContext,
        symbol: str,
        path: str = ".",
        language: str | None = None,
        include_references: bool = False,
        max_results: int = 60,
    ) -> ToolResult:
        root = ctx.resolve_path(path, must_exist=True)
        safe = re.escape(symbol)

        languages = [language] if language else list(_DEFINITION_PATTERNS)
        if language and language not in LANGUAGE_EXTENSIONS:
            raise ToolError(
                f"unknown language {language!r}",
                FailureKind.INVALID_INPUT,
                hint="Known languages: " + ", ".join(sorted(LANGUAGE_EXTENSIONS)),
            )

        patterns: list[str] = []
        for lang in languages:
            for template in _DEFINITION_PATTERNS.get(lang, []):
                patterns.append(template.format(name=safe))
        if not patterns:
            patterns = [rf"\b{safe}\b"]

        globs = None
        if language:
            globs = [f"*{ext}" for ext in LANGUAGE_EXTENSIONS[language]]

        combined = "|".join(f"(?:{p})" for p in patterns)
        definitions, def_total = SearchTextTool._python_search(
            combined,
            root,
            globs=globs,
            regex=True,
            case_sensitive=True,
            max_results=max_results,
            context_lines=0,
            cwd=ctx.cwd,
        )

        sections = []
        if definitions:
            sections.append(f"DEFINITIONS ({def_total}):\n" + "\n".join(definitions))
        else:
            sections.append(f"No definitions found for {symbol!r}.")

        ref_total = 0
        if include_references:
            references, ref_total = SearchTextTool._python_search(
                rf"\b{safe}\b",
                root,
                globs=globs,
                regex=True,
                case_sensitive=True,
                max_results=max_results,
                context_lines=0,
                cwd=ctx.cwd,
            )
            if references:
                sections.append(f"\nREFERENCES ({ref_total}):\n" + "\n".join(references))

        return ToolResult.success(
            "\n".join(sections),
            data={"symbol": symbol, "definitions": def_total, "references": ref_total},
        )


def build_search_tools() -> list[Tool]:
    return [SearchTextTool(), FindFilesTool(), SearchCodeTool()]
