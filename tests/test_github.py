import json

import httpx
import pytest

from prwatch import github
from prwatch.config import Config
from prwatch.github import GitHubError, _build_query, _run_query, fetch_pull_requests
from prwatch.models import CIStatus


def pr_node(number=1, draft=False, rollup="SUCCESS", author="ada"):
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/a/b/pull/{number}",
        "isDraft": draft,
        "createdAt": "2026-08-01T10:00:00Z",
        "updatedAt": "2026-08-20T10:00:00Z",
        "author": {"login": author} if author else None,
        "commits": {
            "nodes": [
                {"commit": {"statusCheckRollup": {"state": rollup} if rollup else None}}
            ]
        },
    }


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_build_query_uses_one_alias_per_repo():
    query = _build_query(["a/b", "c/d"], 25)
    assert 'r0: repository(owner: "a", name: "b")' in query
    assert 'r1: repository(owner: "c", name: "d")' in query
    assert "first: 25" in query


async def test_run_query_parses_pull_requests():
    def handler(request):
        assert json.loads(request.content)["query"].startswith("query {")
        return httpx.Response(
            200,
            json={
                "data": {
                    "r0": {
                        "nameWithOwner": "acme/alpha",
                        "pullRequests": {
                            "nodes": [
                                pr_node(1, rollup="FAILURE"),
                                pr_node(2, draft=True, rollup=None),
                                pr_node(3, rollup="PENDING", author=None),
                            ]
                        },
                    }
                }
            },
        )

    async with make_client(handler) as client:
        result = await _run_query(client, ["acme/alpha"], Config())

    assert [pr.number for pr in result.pull_requests] == [1, 2, 3]
    assert result.pull_requests[0].ci is CIStatus.FAILURE
    assert result.pull_requests[1].ci is CIStatus.NONE
    assert result.pull_requests[1].is_draft
    assert result.pull_requests[2].ci is CIStatus.PENDING
    assert result.pull_requests[2].author == "ghost"
    assert result.pull_requests[0].repo == "acme/alpha"
    assert result.warnings == []


async def test_run_query_reports_per_repo_errors_but_keeps_data():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": {
                    "r0": None,
                    "r1": {
                        "nameWithOwner": "c/d",
                        "pullRequests": {"nodes": [pr_node(7)]},
                    },
                },
                "errors": [
                    {"path": ["r0"], "message": "Could not resolve to a Repository"}
                ],
            },
        )

    async with make_client(handler) as client:
        result = await _run_query(client, ["a/b", "c/d"], Config())

    assert [pr.number for pr in result.pull_requests] == [7]
    assert result.warnings == ["a/b: Could not resolve to a Repository"]


async def test_run_query_raises_when_everything_failed():
    def handler(request):
        return httpx.Response(200, json={"data": None, "errors": [{"message": "boom"}]})

    async with make_client(handler) as client:
        with pytest.raises(GitHubError, match="boom"):
            await _run_query(client, ["a/b"], Config())


@pytest.mark.parametrize(
    "status,match", [(401, "401"), (403, "403"), (500, "HTTP 500")]
)
async def test_run_query_maps_http_errors(status, match):
    async with make_client(lambda request: httpx.Response(status, text="")) as client:
        with pytest.raises(GitHubError, match=match):
            await _run_query(client, ["a/b"], Config())


async def test_run_query_rejects_non_json():
    async with make_client(lambda r: httpx.Response(200, text="<html>")) as client:
        with pytest.raises(GitHubError, match="non-JSON"):
            await _run_query(client, ["a/b"], Config())


async def test_run_query_wraps_transport_errors():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    async with make_client(handler) as client:
        with pytest.raises(GitHubError, match="failed"):
            await _run_query(client, ["a/b"], Config())


async def test_fetch_without_repos_skips_the_network(monkeypatch):
    monkeypatch.setattr(
        github, "resolve_token", lambda cfg: pytest.fail("should not need a token")
    )
    result = await fetch_pull_requests(Config())
    assert result.pull_requests == []


async def test_fetch_batches_repos_and_sorts_by_updated(monkeypatch):
    seen_batches = []

    async def fake_run_query(client, repos, cfg):
        seen_batches.append(list(repos))
        node = pr_node(len(seen_batches))
        node["updatedAt"] = f"2026-08-{10 + len(seen_batches):02d}T00:00:00Z"
        return github.FetchResult([github._parse_pr(repos[0], node)], [])

    monkeypatch.setattr(github, "resolve_token", lambda cfg: "t0ken")
    monkeypatch.setattr(github, "_run_query", fake_run_query)
    cfg = Config(repos=[f"o/r{i}" for i in range(20)])

    result = await fetch_pull_requests(cfg)

    assert [len(batch) for batch in seen_batches] == [8, 8, 4]
    updates = [pr.updated_at for pr in result.pull_requests]
    assert updates == sorted(updates, reverse=True)


@pytest.fixture
def no_token_env(monkeypatch):
    for name in github.TOKEN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_resolve_token_order(no_token_env):
    monkeypatch = no_token_env
    monkeypatch.setattr(github, "_token_from_gh_cli", lambda: "from-gh")
    assert github.resolve_token(Config()) == "from-gh"

    monkeypatch.setenv("GH_TOKEN", "from-env")
    assert github.resolve_token(Config()) == "from-env"

    monkeypatch.setenv("GITHUB_TOKEN", "from-github-token")
    assert github.resolve_token(Config()) == "from-github-token"

    monkeypatch.setenv("PRWATCH_GITHUB_TOKEN", "from-prwatch")
    assert github.resolve_token(Config()) == "from-prwatch"


def test_resolve_token_uses_a_custom_variable_first(no_token_env):
    monkeypatch = no_token_env
    monkeypatch.setattr(github, "_token_from_gh_cli", lambda: None)
    monkeypatch.setenv("GITHUB_TOKEN", "generic")
    monkeypatch.setenv("WORK_TOKEN", "specific")
    assert github.resolve_token(Config(token_env="WORK_TOKEN")) == "specific"
    monkeypatch.delenv("WORK_TOKEN")
    assert github.resolve_token(Config(token_env="WORK_TOKEN")) == "generic"


def test_resolve_token_ignores_a_blank_variable(no_token_env):
    monkeypatch = no_token_env
    monkeypatch.setattr(github, "_token_from_gh_cli", lambda: "from-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert github.resolve_token(Config()) == "from-gh"


def test_resolve_token_without_any_source(no_token_env):
    no_token_env.setattr(github, "_token_from_gh_cli", lambda: None)
    with pytest.raises(GitHubError, match=r"no GitHub token.*PRWATCH_GITHUB_TOKEN"):
        github.resolve_token(Config())
