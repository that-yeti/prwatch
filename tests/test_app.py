from datetime import UTC, datetime, timedelta

import pytest
from textual.widgets import DataTable, Input, ListView, RichLog, Static

from prwatch import app as app_module
from prwatch.app import PRWatchApp
from prwatch.config import Config, load_config
from prwatch.github import FetchResult, GitHubError
from prwatch.models import CIStatus, PullRequest

# Ages are rendered against the wall clock, so anchor the fixtures to it.
NOW = datetime.now(UTC)


def make_pr(repo="acme/alpha", number=1, ci=CIStatus.SUCCESS, draft=False, title=None, hours=1):
    return PullRequest(
        repo=repo,
        number=number,
        title=title or f"Change {number}",
        url=f"https://github.com/{repo}/pull/{number}",
        author="ada",
        is_draft=draft,
        created_at=NOW - timedelta(days=number),
        updated_at=NOW - timedelta(hours=hours),
        ci=ci,
    )


SAMPLE = [
    make_pr(number=1, ci=CIStatus.SUCCESS, hours=1),
    make_pr(number=2, ci=CIStatus.FAILURE, hours=2, repo="zeta/beta"),
    make_pr(number=3, ci=CIStatus.PENDING, hours=3, title="Flaky pipeline"),
    make_pr(number=4, ci=CIStatus.SUCCESS, draft=True, hours=4),
    make_pr(number=5, ci=CIStatus.NONE, hours=5),
]


@pytest.fixture
def cfg(isolated_config):
    return Config(repos=["acme/alpha", "zeta/beta"], path=isolated_config)


@pytest.fixture
def stub_fetch(monkeypatch):
    state = {"prs": SAMPLE, "warnings": [], "error": None, "calls": 0}

    async def fake_fetch(config):
        state["calls"] += 1
        if state["error"]:
            raise state["error"]
        return FetchResult(list(state["prs"]), list(state["warnings"]))

    monkeypatch.setattr(app_module, "fetch_pull_requests", fake_fetch)
    return state


def rows(app):
    table = app.query_one("#prs", DataTable)
    return [table.get_row_at(index) for index in range(table.row_count)]


def widget_text(widget: Static) -> str:
    return str(widget.content)


def status_text(app):
    return widget_text(app.query_one("#status", Static))


async def test_table_lists_prs_newest_first_with_status_colours(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        table_rows = rows(app)
        assert [row[2].plain for row in table_rows] == ["#1", "#2", "#3", "#4", "#5"]
        assert [row[1].plain for row in table_rows][:2] == ["acme/alpha", "zeta/beta"]
        assert [row[3].style for row in table_rows] == [
            "green",
            "red",
            "yellow",
            "grey50",
            "white",
        ]
        assert table_rows[0][4].plain == "1d"
        assert table_rows[0][5].plain == "1h"
        assert "5/5 PRs" in status_text(app)
        assert "2 repos" in status_text(app)


async def test_filter_narrows_rows_and_escape_restores(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        field = app.query_one("#filter", Input)
        assert field.has_focus
        field.value = "flaky"
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#3"]
        assert "1/5 PRs" in status_text(app)

        await pilot.press("escape")
        await pilot.pause()
        assert len(rows(app)) == 5
        assert app.query_one("#prs", DataTable).has_focus


async def test_filter_matches_repo_and_number(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        app.query_one("#filter", Input).value = "zeta"
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#2"]
        app.query_one("#filter", Input).value = "#5"
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#5"]


async def test_toggle_drafts(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#1", "#2", "#3", "#5"]
        assert "drafts hidden" in status_text(app)
        await pilot.press("d")
        await pilot.pause()
        assert len(rows(app)) == 5


async def test_sort_cycles_and_reverses(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("o")  # created, descending
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#1", "#2", "#3", "#4", "#5"]
        await pilot.press("O")
        await pilot.pause()
        assert [row[2].plain for row in rows(app)] == ["#5", "#4", "#3", "#2", "#1"]
        await pilot.press("o")  # repo, ascending
        await pilot.pause()
        assert [row[1].plain for row in rows(app)][-1] == "zeta/beta"


async def test_ci_sort_puts_failures_first(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(5):
            await pilot.press("o")
        await pilot.press("O")  # ci, ascending
        await pilot.pause()
        assert "sort CI state" in status_text(app)
        assert next(row[2].plain for row in rows(app)) == "#2"


async def test_cursor_survives_a_refresh(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#prs", DataTable)
        table.move_cursor(row=2)
        stub_fetch["prs"] = [make_pr(number=9, hours=0), *SAMPLE]
        await pilot.press("r")
        await pilot.pause()
        assert table.cursor_row == 3
        assert app._selected_pr().number == 3


async def test_fetch_error_is_shown_in_the_status_bar(cfg, stub_fetch):
    stub_fetch["error"] = GitHubError("no GitHub token found")
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "no GitHub token found" in status_text(app)
        assert app.query_one("#status", Static).has_class("error")


async def test_repo_warnings_are_surfaced(cfg, stub_fetch):
    stub_fetch["warnings"] = ["acme/alpha: Could not resolve to a Repository"]
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Could not resolve" in status_text(app)


async def test_no_repos_configured_prompts_the_user(isolated_config, stub_fetch):
    app = PRWatchApp(Config(path=isolated_config))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "no repositories configured" in status_text(app)
        assert stub_fetch["calls"] == 0


async def test_open_pr_fires_once_per_enter(cfg, stub_fetch, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(PRWatchApp, "_open_url", lambda self, url: opened.append(url))
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert opened == ["https://github.com/acme/alpha/pull/1"]


async def test_copy_url(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.clipboard == "https://github.com/acme/alpha/pull/1"


async def test_add_repo_persists_and_refreshes(cfg, stub_fetch, isolated_config):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one(Input).value = "https://github.com/new/repo/pull/3"
        await pilot.press("enter")
        await pilot.pause()
        assert "new/repo" in app.cfg.repos
        assert "new/repo" in load_config(isolated_config).repos
        assert stub_fetch["calls"] == 2


async def test_add_repo_rejects_a_duplicate(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one(Input).value = "acme/alpha"
        await pilot.press("enter")
        await pilot.pause()
        assert app.cfg.repos == ["acme/alpha", "zeta/beta"]
        assert any("already configured" in str(n.message) for n in app._notifications)


async def test_manage_repos_removes_after_confirmation(cfg, stub_fetch, isolated_config):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        listing = app.screen.query_one(ListView)
        listing.index = 1
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.cfg.repos == ["acme/alpha"]
        assert [pr.repo for pr in app._prs] == ["acme/alpha"] * 4
        assert load_config(isolated_config).repos == ["acme/alpha"]
        await pilot.press("escape")
        await pilot.pause()
        assert stub_fetch["calls"] == 2


async def test_manage_repos_keeps_repo_when_declined(cfg, stub_fetch):
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.cfg.repos == ["acme/alpha", "zeta/beta"]


async def test_summary_runs_the_single_configured_agent(cfg, stub_fetch):
    cfg.agents = {"echo": ["echo", "summary of {prompt}"]}
    cfg.default_agent = "echo"
    cfg.prompts["summary"] = "{repo}#{number}"
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        log = app.screen.query_one(RichLog)
        await _wait_for(pilot, lambda: "[exit 0]" in _log_text(log))
        assert "summary of acme/alpha#1" in _log_text(log)
        assert "exit 0" in widget_text(app.screen.query_one("#output-hint", Static))


async def test_review_asks_which_agent_to_use(cfg, stub_fetch):
    cfg.agents = {"echo": ["echo", "{prompt}"], "cat": ["cat"]}
    cfg.default_agent = "echo"
    cfg.prompts["review"] = "review {number}"
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert app.screen.query_one(ListView).index == 1  # "echo", sorted after "cat"
        await pilot.press("enter")
        await pilot.pause()
        log = app.screen.query_one(RichLog)
        await _wait_for(pilot, lambda: "[exit 0]" in _log_text(log))
        assert "review 1" in _log_text(log)


async def test_agent_action_reports_a_missing_binary(cfg, stub_fetch):
    cfg.agents = {"ghost": ["definitely-not-a-real-binary-xyz", "{prompt}"]}
    cfg.default_agent = "ghost"
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert any("not on PATH" in str(n.message) for n in app._notifications)


async def test_escape_stops_a_running_agent(cfg, stub_fetch):
    cfg.agents = {"sleeper": ["sleep", "60"]}
    cfg.default_agent = "sleeper"
    app = PRWatchApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen.query_one(RichLog), RichLog)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.screen_stack[-1]
        assert not isinstance(app.screen, app_module.OutputScreen)


def _log_text(log: RichLog) -> str:
    return "\n".join(
        segment.text
        for line in log.lines
        for segment in line._segments
    )


async def _wait_for(pilot, predicate, tries=100):
    for _ in range(tries):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not met in time")
