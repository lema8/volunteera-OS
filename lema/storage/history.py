"""Persistent prompt history for the interactive REPL."""

from __future__ import annotations

from pathlib import Path

from lema.util import paths


def history_file() -> Path:
    directory = paths.ensure_dir(paths.history_dir())
    return directory / "prompts.txt"


def load_recent(limit: int = 50) -> list[str]:
    path = history_file()
    if not path.is_file():
        return []
    try:
        lines = [ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []
    return [ln for ln in lines if ln.strip()][-limit:]
