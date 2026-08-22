from prwatch.cli import main
from prwatch.config import load_config


def test_config_prints_the_path(isolated_config, capsys):
    assert main(["config"]) == 0
    assert capsys.readouterr().out.strip() == str(isolated_config)


def test_add_writes_the_config(isolated_config, capsys):
    assert main(["add", "acme/alpha", "https://github.com/zeta/beta"]) == 0
    assert load_config(isolated_config).repos == ["acme/alpha", "zeta/beta"]
    out = capsys.readouterr().out
    assert str(isolated_config) in out
    assert "acme/alpha" in out


def test_add_reports_bad_input_without_aborting(isolated_config, capsys):
    assert main(["add", "not-a-repo", "acme/alpha"]) == 0
    captured = capsys.readouterr()
    assert "'not-a-repo' is not an owner/name repository reference" in captured.err
    assert load_config(isolated_config).repos == ["acme/alpha"]


def test_remove_and_list(isolated_config, capsys):
    main(["add", "acme/alpha", "zeta/beta"])
    capsys.readouterr()
    assert main(["rm", "acme/alpha"]) == 0
    capsys.readouterr()
    assert main(["ls"]) == 0
    assert capsys.readouterr().out.strip() == "zeta/beta"


def test_list_when_empty_points_at_the_config(isolated_config, capsys):
    assert main(["list"]) == 0
    assert "no repositories configured" in capsys.readouterr().out


def test_broken_config_exits_nonzero(isolated_config, capsys):
    isolated_config.write_text("repos = 3", encoding="utf-8")
    assert main(["list"]) == 1
    assert "prwatch:" in capsys.readouterr().err


def test_explicit_config_flag_wins(tmp_path, capsys):
    other = tmp_path / "other.toml"
    assert main(["--config", str(other), "add", "acme/alpha"]) == 0
    assert load_config(other).repos == ["acme/alpha"]


def test_launching_the_tui_seeds_a_config_file(isolated_config, monkeypatch):
    launched = {}
    from prwatch import app as app_module

    monkeypatch.setattr(
        app_module.PRWatchApp, "run", lambda self: launched.setdefault("cfg", self.cfg)
    )
    assert main([]) == 0
    assert isolated_config.exists()
    assert launched["cfg"].repos == []
