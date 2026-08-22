from datetime import UTC, datetime, timedelta

import pytest

from prwatch.actions import ActionError, build_command, render_prompt, stream_command
from prwatch.config import Config
from prwatch.models import CIStatus, PullRequest, humanise_age

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def make_pr(**overrides) -> PullRequest:
    base = {
        "repo": "acme/alpha",
        "number": 7,
        "title": "Add widgets",
        "url": "https://github.com/acme/alpha/pull/7",
        "author": "ada",
        "is_draft": False,
        "created_at": NOW - timedelta(days=3),
        "updated_at": NOW - timedelta(hours=2),
        "ci": CIStatus.SUCCESS,
    }
    return PullRequest(**{**base, **overrides})


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESS", CIStatus.SUCCESS),
        ("PENDING", CIStatus.PENDING),
        ("EXPECTED", CIStatus.PENDING),
        ("FAILURE", CIStatus.FAILURE),
        ("ERROR", CIStatus.FAILURE),
        (None, CIStatus.NONE),
        ("SOMETHING_NEW", CIStatus.NONE),
    ],
)
def test_ci_status_mapping(state, expected):
    assert CIStatus.from_rollup(state) is expected


@pytest.mark.parametrize(
    "pr,style",
    [
        (make_pr(), "green"),
        (make_pr(ci=CIStatus.PENDING), "yellow"),
        (make_pr(ci=CIStatus.FAILURE), "red"),
        (make_pr(ci=CIStatus.NONE), "green"),
        (make_pr(is_draft=True, ci=CIStatus.FAILURE), "grey50"),
        (make_pr(is_draft=True, ci=CIStatus.NONE), "grey50"),
    ],
)
def test_row_style(pr, style):
    assert pr.style == style


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=5), "5s"),
        (timedelta(minutes=3), "3m"),
        (timedelta(hours=5), "5h"),
        (timedelta(days=9), "9d"),
        (timedelta(days=800), "2y"),
        (timedelta(seconds=-30), "0s"),
    ],
)
def test_humanise_age(delta, expected):
    assert humanise_age(NOW - delta, now=NOW) == expected


def test_render_prompt_fills_placeholders():
    cfg = Config()
    cfg.prompts["summary"] = "{repo}#{number} {title} by {author} at {url} ({state})"
    assert render_prompt(cfg, "summary", make_pr()) == (
        "acme/alpha#7 Add widgets by ada at https://github.com/acme/alpha/pull/7 (success)"
    )


def test_render_prompt_reports_unknown_placeholder():
    cfg = Config()
    cfg.prompts["summary"] = "{nope}"
    with pytest.raises(ActionError, match="unknown placeholder"):
        render_prompt(cfg, "summary", make_pr())


def test_build_command_substitutes_prompt():
    cfg = Config(agents={"echo": ["echo", "--prompt={prompt}"]})
    assert build_command(cfg, "echo", "hi") == ["echo", "--prompt=hi"]


def test_build_command_appends_prompt_when_no_placeholder():
    cfg = Config(agents={"echo": ["echo", "-p"]})
    assert build_command(cfg, "echo", "hi") == ["echo", "-p", "hi"]


def test_build_command_requires_the_binary_on_path():
    cfg = Config(agents={"nope": ["definitely-not-a-real-binary-xyz"]})
    with pytest.raises(ActionError, match="not on PATH"):
        build_command(cfg, "nope", "hi")


async def test_stream_command_streams_lines_and_merges_stderr():
    lines: list[str] = []
    code = await stream_command(
        ["sh", "-c", "echo one; echo two >&2; exit 3"], lines.append
    )
    assert code == 3
    assert sorted(lines) == ["one", "two"]


async def test_stream_command_reports_a_missing_binary():
    with pytest.raises(ActionError, match="could not start"):
        await stream_command(["definitely-not-a-real-binary-xyz"], print)
