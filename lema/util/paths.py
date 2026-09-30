"""XDG-compliant path resolution for Lema Harness.

Everything is overridable through environment variables so that tests and
sandboxed runs never touch the real user home directory.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "lema"

#: Environment variable that relocates *all* of Lema's global state at once.
ENV_HOME = "LEMA_HOME"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    return Path(raw).expanduser()


def lema_home() -> Path | None:
    """A single directory that overrides config/state/data roots when set."""
    return _env_path(ENV_HOME)


def config_dir() -> Path:
    """Global configuration directory, e.g. ``~/.config/lema``."""
    override = _env_path("LEMA_CONFIG_DIR")
    if override:
        return override
    home = lema_home()
    if home:
        return home / "config"
    base = _env_path("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / APP


def state_dir() -> Path:
    """Logs and session state, e.g. ``~/.local/state/lema``."""
    override = _env_path("LEMA_STATE_DIR")
    if override:
        return override
    home = lema_home()
    if home:
        return home / "state"
    base = _env_path("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return base / APP


def data_dir() -> Path:
    """Durable data, e.g. ``~/.local/share/lema``."""
    override = _env_path("LEMA_DATA_DIR")
    if override:
        return override
    home = lema_home()
    if home:
        return home / "data"
    base = _env_path("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return base / APP


def global_config_file() -> Path:
    return config_dir() / "config.toml"


def global_skills_dir() -> Path:
    return config_dir() / "skills"


def global_providers_dir() -> Path:
    return config_dir() / "providers"


def global_tools_dir() -> Path:
    """User-authored Python tool plugins."""
    return config_dir() / "tools"


def history_dir() -> Path:
    return config_dir() / "history"


def project_config_dir(root: Path) -> Path:
    return Path(root) / ".lema"


def project_config_file(root: Path) -> Path:
    return project_config_dir(root) / "config.toml"


def project_skills_dir(root: Path) -> Path:
    return project_config_dir(root) / "skills"


def project_tools_dir(root: Path) -> Path:
    return project_config_dir(root) / "tools"


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_project_root(start: Path | None = None) -> Path:
    """Walk upwards looking for a project marker; fall back to ``start``.

    A ``.lema`` directory wins over ``.git`` so that nested tool projects can
    declare their own agent workspace.
    """
    start = Path(start or Path.cwd()).resolve()
    candidates = [start, *start.parents]
    for marker in (".lema", ".git"):
        for candidate in candidates:
            if (candidate / marker).exists():
                return candidate
    return start


def display_path(path: Path | str) -> str:
    """Render an absolute location for a header line.

    Unlike :func:`shorten_path` this never returns ``"."``: a banner that says
    the working directory is "." tells the user nothing.
    """
    p = Path(path)
    try:
        relative = p.resolve().relative_to(Path.home())
    except ValueError:
        return str(p)
    return "~" if str(relative) == "." else f"~/{relative}"


def shorten_path(path: Path | str, base: Path | None = None) -> str:
    """Render a path for humans: relative to cwd when possible, else ``~``-ised."""
    p = Path(path)
    base = Path(base or Path.cwd())
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        pass
    try:
        return "~/" + str(p.resolve().relative_to(Path.home()))
    except ValueError:
        return str(p)
