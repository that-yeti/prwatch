import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep every test off the real ~/.config/prwatch/config.toml."""
    monkeypatch.setenv("PRWATCH_CONFIG", str(tmp_path / "config.toml"))
    return tmp_path / "config.toml"
