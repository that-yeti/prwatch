"""Minimal GitHub GraphQL client: open pull requests plus their CI rollup."""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from datetime import datetime

import httpx

from .config import Config
from .models import CIStatus, PullRequest

REPOS_PER_QUERY = 8
TIMEOUT = httpx.Timeout(30.0)

_PR_FIELDS = """
    number
    title
    url
    isDraft
    createdAt
    updatedAt
    author { login }
    commits(last: 1) {
      nodes {
        commit {
          statusCheckRollup { state }
        }
      }
    }
"""


class GitHubError(Exception):
    """Raised when the whole fetch fails (auth, network, malformed response)."""


@dataclass(frozen=True)
class FetchResult:
    pull_requests: list[PullRequest]
    warnings: list[str]


TOKEN_ENV_VARS = ("PRWATCH_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")


def token_env_vars(cfg: Config) -> tuple[str, ...]:
    """The environment variables searched for a token, most specific first."""
    if cfg.token_env:
        return (cfg.token_env, *TOKEN_ENV_VARS)
    return TOKEN_ENV_VARS


def resolve_token(cfg: Config) -> str:
    """Find a GitHub token in the environment, then fall back to the gh CLI.

    Tokens are deliberately never read from the config file.
    """
    names = token_env_vars(cfg)
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    token = _token_from_gh_cli()
    if token:
        return token
    raise GitHubError(
        f"no GitHub token found - export ${names[0]} or run 'gh auth login' "
        "(prwatch never reads a token from its config file)"
    )


def _token_from_gh_cli() -> str | None:
    import subprocess

    if not shutil.which("gh"):
        return None
    try:
        done = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = done.stdout.strip()
    return token if done.returncode == 0 and token else None


def _build_query(repos: list[str], limit: int) -> str:
    blocks = []
    for index, repo in enumerate(repos):
        owner, name = repo.split("/", 1)
        blocks.append(
            f'  r{index}: repository(owner: "{owner}", name: "{name}") {{\n'
            f"    nameWithOwner\n"
            f"    pullRequests(states: OPEN, first: {limit}, "
            f"orderBy: {{field: UPDATED_AT, direction: DESC}}) {{\n"
            f"      nodes {{{_PR_FIELDS}}}\n"
            f"    }}\n"
            f"  }}"
        )
    return "query {\n" + "\n".join(blocks) + "\n}"


def _parse_timestamp(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp.

    ``datetime.fromisoformat`` only learned to accept a trailing ``Z`` in 3.11,
    and every timestamp GitHub returns carries one.
    """
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _parse_pr(repo: str, node: dict) -> PullRequest:
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = None
    if commits:
        rollup = ((commits[0] or {}).get("commit") or {}).get("statusCheckRollup")
    return PullRequest(
        repo=repo,
        number=node["number"],
        title=node["title"] or "",
        url=node["url"],
        author=((node.get("author") or {}).get("login")) or "ghost",
        is_draft=bool(node.get("isDraft")),
        created_at=_parse_timestamp(node["createdAt"]),
        updated_at=_parse_timestamp(node["updatedAt"]),
        ci=CIStatus.from_rollup((rollup or {}).get("state")),
    )


async def _run_query(
    client: httpx.AsyncClient, repos: list[str], cfg: Config
) -> FetchResult:
    query = _build_query(repos, cfg.max_prs_per_repo)
    try:
        response = await client.post(cfg.api_url, json={"query": query})
    except httpx.HTTPError as exc:
        raise GitHubError(f"request to {cfg.api_url} failed: {exc}") from exc

    if response.status_code == 401:
        raise GitHubError("GitHub rejected the token (401) - is it expired?")
    if response.status_code == 403:
        raise GitHubError("GitHub returned 403 - token lacks scope, or rate limited")
    if response.status_code >= 400:
        raise GitHubError(f"GitHub returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise GitHubError(f"GitHub returned a non-JSON response: {exc}") from exc

    data = payload.get("data") or {}
    warnings: list[str] = []
    for error in payload.get("errors") or []:
        path = error.get("path") or []
        alias = str(path[0]) if path else ""
        repo = repos[int(alias[1:])] if alias.startswith("r") and alias[1:].isdigit() else None
        message = error.get("message", "unknown GraphQL error")
        warnings.append(f"{repo}: {message}" if repo else message)
    if not data and warnings:
        raise GitHubError("; ".join(warnings))

    pull_requests: list[PullRequest] = []
    for index, repo in enumerate(repos):
        node = data.get(f"r{index}")
        if not node:
            continue
        repo_name = node.get("nameWithOwner") or repo
        for pr_node in (node.get("pullRequests") or {}).get("nodes") or []:
            if pr_node:
                pull_requests.append(_parse_pr(repo_name, pr_node))
    return FetchResult(pull_requests, warnings)


async def fetch_pull_requests(cfg: Config) -> FetchResult:
    """Fetch every open PR for the configured repos, batched into few requests."""
    if not cfg.repos:
        return FetchResult([], [])
    token = resolve_token(cfg)
    batches = [
        cfg.repos[i : i + REPOS_PER_QUERY]
        for i in range(0, len(cfg.repos), REPOS_PER_QUERY)
    ]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "prwatch",
    }
    async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *(_run_query(client, batch, cfg) for batch in batches)
        )

    pull_requests: list[PullRequest] = []
    warnings: list[str] = []
    for result in results:
        pull_requests.extend(result.pull_requests)
        warnings.extend(result.warnings)
    pull_requests.sort(key=lambda pr: pr.updated_at, reverse=True)
    return FetchResult(pull_requests, warnings)
