"""Configuration loading, layering and persistence."""

from __future__ import annotations

import os

import pytest

from lema.config.loader import (
    ConfigError,
    apply_overrides,
    default_config_toml,
    load_config,
    save_global_config,
    write_default_global_config,
)
from lema.config.schema import Config, PermissionMode, ProviderConfig
from lema.util.paths import config_dir, find_project_root, state_dir


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_defaults_are_usable_without_any_config_file(tmp_path):
    config = load_config(cwd=tmp_path)
    assert config.provider.kind == "ollama"
    assert config.agent.max_iterations >= 50
    assert config.provider.context_window > 0
    assert isinstance(config.permissions, PermissionMode)


def test_global_config_is_read(tmp_path):
    write(
        config_dir() / "config.toml",
        """
        [provider]
        kind = "openai"
        model = "gpt-4o-mini"
        temperature = 0.3

        [agent]
        max_iterations = 250
        """,
    )
    config = load_config(cwd=tmp_path)
    assert config.provider.kind == "openai"
    assert config.provider.model == "gpt-4o-mini"
    assert config.provider.temperature == 0.3
    assert config.agent.max_iterations == 250


def test_project_config_overrides_global(tmp_path):
    write(config_dir() / "config.toml", '[provider]\nmodel = "global-model"\ntemperature = 0.1\n')
    project = tmp_path / "proj"
    write(project / ".lema" / "config.toml", '[provider]\nmodel = "project-model"\n')
    config = load_config(cwd=project)
    assert config.provider.model == "project-model"
    assert config.provider.temperature == 0.1  # inherited, not clobbered
    assert config.project_root == project


def test_named_provider_profiles(tmp_path):
    write(
        config_dir() / "providers" / "work.toml",
        """
        kind = "anthropic"
        model = "claude-sonnet-4"
        api_key_env = "WORK_KEY"
        """,
    )
    write(config_dir() / "config.toml", '[provider]\nname = "work"\n')
    config = load_config(cwd=tmp_path)
    assert config.provider.kind == "anthropic"
    assert config.provider.model == "claude-sonnet-4"
    assert "work" in config.providers


def test_selecting_a_profile_at_runtime(tmp_path):
    write(config_dir() / "providers" / "fast.toml", 'kind = "ollama"\nmodel = "qwen2.5-coder:7b"\n')
    write(config_dir() / "providers" / "big.toml", 'kind = "openai"\nmodel = "gpt-4o"\n')
    config = load_config(cwd=tmp_path, provider_name="big")
    assert config.provider.model == "gpt-4o"
    assert sorted(config.providers) == ["big", "fast"]


def test_unknown_profile_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="unknown provider profile"):
        load_config(cwd=tmp_path, provider_name="ghost")


def test_environment_variables_override_files(tmp_path, monkeypatch):
    write(config_dir() / "config.toml", '[provider]\nmodel = "from-file"\n')
    monkeypatch.setenv("LEMA_MODEL", "from-env")
    monkeypatch.setenv("LEMA_PROVIDER", "openai")
    monkeypatch.setenv("LEMA_BASE_URL", "http://example:1234/v1")
    config = load_config(cwd=tmp_path)
    assert config.provider.model == "from-env"
    assert config.provider.kind == "openai"
    assert config.provider.base_url == "http://example:1234/v1"


def test_cli_overrides_beat_everything(tmp_path, monkeypatch):
    write(config_dir() / "config.toml", '[agent]\nmax_iterations = 10\n')
    monkeypatch.setenv("LEMA_MODEL", "env-model")
    config = load_config(
        cwd=tmp_path,
        overrides=["agent.max_iterations=999", "provider.model=cli-model"],
    )
    assert config.agent.max_iterations == 999
    assert config.provider.model == "cli-model"


@pytest.mark.parametrize(
    "override,attr,expected",
    [
        ("agent.max_iterations=42", ("agent", "max_iterations"), 42),
        ("provider.temperature=0.75", ("provider", "temperature"), 0.75),
        ("provider.stream=false", ("provider", "stream"), False),
        ("provider.stream=true", ("provider", "stream"), True),
        ("ui.banner=no", ("ui", "banner"), False),
        ("provider.model=abc", ("provider", "model"), "abc"),
    ],
)
def test_override_type_coercion(tmp_path, override, attr, expected):
    config = load_config(cwd=tmp_path, overrides=[override])
    section = getattr(config, attr[0])
    assert getattr(section, attr[1]) == expected


def test_bad_override_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="unknown setting"):
        load_config(cwd=tmp_path, overrides=["agent.nonsense=1"])
    with pytest.raises(ConfigError, match="expected"):
        load_config(cwd=tmp_path, overrides=["agent.max_iterations=lots"])
    with pytest.raises(ConfigError, match="section.key=value"):
        load_config(cwd=tmp_path, overrides=["garbage"])


def test_malformed_toml_names_the_file(tmp_path):
    path = config_dir() / "config.toml"
    write(path, "[provider\nmodel = ")
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path)
    assert "config.toml" in str(exc.value)


def test_permissions_can_be_set_from_config(tmp_path):
    write(config_dir() / "config.toml", 'permissions = "readonly"\n')
    assert load_config(cwd=tmp_path).permissions is PermissionMode.READONLY


def test_write_and_reload_default_config(tmp_path):
    path = write_default_global_config()
    assert path.exists()
    assert "[provider]" in path.read_text()
    config = load_config(cwd=tmp_path)
    assert config.provider.model


def test_default_config_toml_is_valid_and_documented():
    text = default_config_toml()
    assert "#" in text  # has comments
    import tomllib

    parsed = tomllib.loads(text)
    assert "provider" in parsed and "agent" in parsed


def test_save_config_round_trip(tmp_path):
    config = load_config(cwd=tmp_path)
    config.provider.model = "round-trip-model"
    config.agent.max_iterations = 77
    save_global_config(config)
    reloaded = load_config(cwd=tmp_path)
    assert reloaded.provider.model == "round-trip-model"
    assert reloaded.agent.max_iterations == 77


def test_project_root_detection(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    nested = tmp_path / "repo" / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path / "repo"


def test_project_root_prefers_dot_lema(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    inner = tmp_path / "repo" / "sub"
    (inner / ".lema").mkdir(parents=True)
    assert find_project_root(inner) == inner


def test_paths_are_isolated_by_lema_home(tmp_path, monkeypatch):
    """The isolated_home fixture must really redirect every location."""
    home = os.environ["LEMA_HOME"]
    assert str(config_dir()).startswith(home)
    assert str(state_dir()).startswith(home)
    assert config_dir() != state_dir()


def test_xdg_paths_when_no_lema_home(monkeypatch, tmp_path):
    monkeypatch.delenv("LEMA_HOME", raising=False)
    monkeypatch.delenv("LEMA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LEMA_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert config_dir() == tmp_path / "cfg" / "lema"
    assert state_dir() == tmp_path / "state" / "lema"


def test_context_window_follows_the_provider(tmp_path):
    config = load_config(cwd=tmp_path, overrides=["provider.context_window=8192"])
    assert config.provider.context_window == 8192
    assert 0 < config.context.compaction_threshold < 1


def test_apply_overrides_on_an_existing_object():
    config = Config()
    apply_overrides(config, ["ui.color=false", "agent.max_iterations=5"])
    assert config.ui.color is False
    assert config.agent.max_iterations == 5


def test_provider_config_defaults_by_kind():
    assert ProviderConfig(kind="ollama").effective_base_url().endswith("11434")
    assert "api.openai.com" in ProviderConfig(kind="openai").effective_base_url()
    assert "anthropic.com" in ProviderConfig(kind="anthropic").effective_base_url()
