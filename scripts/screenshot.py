#!/usr/bin/env python
"""Render docs/screenshot.svg from stub data (no network, no config needed).

Usage: python scripts/screenshot.py [output.svg] [key ...]
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prwatch import app as app_module
from prwatch.app import PRWatchApp
from prwatch.config import Config
from prwatch.github import FetchResult
from prwatch.models import CIStatus, PullRequest

NOW = datetime.now(UTC)


def pr(repo, number, title, ci, *, draft=False, days=1, hours=1, author="ada"):
    return PullRequest(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/pull/{number}",
        author=author,
        is_draft=draft,
        created_at=NOW - timedelta(days=days),
        updated_at=NOW - timedelta(hours=hours),
        ci=ci,
    )


SAMPLE = [
    pr("acme/alpha", 812, "Cache the resolver results between runs", CIStatus.SUCCESS, days=2, hours=1),
    pr("acme/alpha", 809, "Drop the legacy config loader", CIStatus.FAILURE, days=4, hours=3),
    pr("zeta/beta", 47, "Bump tokio to 1.40 and fix the deprecations", CIStatus.PENDING, days=1, hours=5),
    pr("zeta/beta", 44, "WIP: streaming diff view", CIStatus.SUCCESS, draft=True, days=9, hours=26),
    pr("ity/olly", 3, "Add architecture notes", CIStatus.NONE, days=30, hours=90, author="ity"),
    pr("ity/olly", 2, "Initial CLI skeleton", CIStatus.SUCCESS, days=45, hours=200, author="ity"),
]


async def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshot.svg")
    keys = sys.argv[2:]

    async def fake_fetch(cfg):
        return FetchResult(SAMPLE, [])

    app_module.fetch_pull_requests = fake_fetch
    cfg = Config(repos=["acme/alpha", "zeta/beta", "ity/olly"], path=Path("/dev/null"))
    app = PRWatchApp(cfg)
    async with app.run_test(size=(110, 22)) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause(0.2)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(app.export_screenshot())
    print(f"wrote {output}")


if __name__ == "__main__":
    asyncio.run(main())
