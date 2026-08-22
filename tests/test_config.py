import pytest

from prwatch.config import (
    Config,
    ConfigError,
    config_path,
    load_config,
    normalise_repo,
    save_config,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("owner/name", "owner/name"),
        ("  owner/name  ", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("https://github.com/owner/name/pull/42", "owner/name"),
        ("git@github.com:owner/name.git", "owner/name"),
        ("owner/name.git", "owner/name"),
    ],
)
def test_normalise_repo(raw, expected):
    assert normalise_repo(raw) == expected


@pytest.mark.parametrize("raw", ["", "nameonly", "a/b/c/d", "own er/name", "/name"])
def test_normalise_repo_rejects_junk(raw):
    with pytest.raises(ConfigError):
        normalise_repo(raw)


def test_missing_file_yields_defaults(isolated_config):
    cfg = load_config()
    assert cfg.repos == []
    assert cfg.default_agent == "claude"
    assert cfg.path == isolated_config


def test_add_and_remove_repo():
    cfg = Config().with_repo("https://github.com/Zed/Beta")
    cfg = cfg.with_repo("acme/alpha")
    assert cfg.repos == ["acme/alpha", "Zed/Beta"]

    with pytest.raises(ConfigError):
        cfg.with_repo("ACME/ALPHA")
    with pytest.raises(ConfigError):
        cfg.without_repo("nope/nope")
    assert cfg.without_repo("ACME/alpha").repos == ["Zed/Beta"]


def test_round_trip_preserves_awkward_values(isolated_config):
    cfg = Config(repos=["a/b"], refresh_seconds=60)
    cfg.prompts["summary"] = 'Say "hi"\nthen stop\\now\t.'
    cfg.agents["my.agent"] = ["some-cli", "--prompt", "{prompt}"]
    cfg.default_agent = "my.agent"
    save_config(cfg, isolated_config)

    loaded = load_config(isolated_config)
    assert loaded.repos == ["a/b"]
    assert loaded.refresh_seconds == 60
    assert loaded.prompts["summary"] == 'Say "hi"\nthen stop\\now\t.'
    assert loaded.agents["my.agent"] == ["some-cli", "--prompt", "{prompt}"]
    assert loaded.default_agent == "my.agent"


def test_defaults_are_merged_not_replaced(isolated_config):
    isolated_config.write_text(
        'repos = ["a/b"]\n[agents.mine]\ncommand = ["mine"]\n', encoding="utf-8"
    )
    cfg = load_config()
    assert set(cfg.agents) == {"claude", "copilot", "mine"}
    assert set(cfg.prompts) == {"summary", "review"}


@pytest.mark.parametrize(
    "body",
    [
        "repos = 'notalist'",
        "refresh_seconds = 0",
        "refresh_seconds = true",
        'default_agent = "ghost"',
        "[agents.broken]\ncommand = 3",
        "[prompts]\nsummary = 12",
        "this is not toml",
    ],
)
def test_bad_config_raises(isolated_config, body):
    isolated_config.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config()


def test_config_path_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRWATCH_CONFIG", str(tmp_path / "x.toml"))
    assert config_path() == tmp_path / "x.toml"
    monkeypatch.delenv("PRWATCH_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "prwatch" / "config.toml"


def test_saved_config_is_not_world_readable(isolated_config):
    save_config(Config(repos=["acme/alpha"]), isolated_config)
    assert isolated_config.stat().st_mode & 0o777 == 0o600


def test_a_token_in_the_config_file_is_rejected(isolated_config):
    isolated_config.write_text('repos = []\ntoken = "ghp_secret"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        load_config(isolated_config)


def test_token_env_names_a_variable_and_round_trips(isolated_config):
    save_config(Config(token_env="WORK_TOKEN"), isolated_config)
    assert "ghp_" not in isolated_config.read_text(encoding="utf-8")
    assert load_config(isolated_config).token_env == "WORK_TOKEN"
