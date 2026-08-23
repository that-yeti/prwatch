"""Data types shared between the GitHub client and the UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class CIStatus(Enum):
    """Rolled up state of the CI checks on a pull request's head commit."""

    SUCCESS = "success"
    PENDING = "pending"
    FAILURE = "failure"
    NONE = "none"

    @classmethod
    def from_rollup(cls, state: str | None) -> CIStatus:
        """Map a GitHub ``StatusState`` onto our four buckets."""
        if state == "SUCCESS":
            return cls.SUCCESS
        if state in ("PENDING", "EXPECTED"):
            return cls.PENDING
        if state in ("FAILURE", "ERROR"):
            return cls.FAILURE
        return cls.NONE

    @property
    def symbol(self) -> str:
        return {
            CIStatus.SUCCESS: "✓",
            CIStatus.PENDING: "•",
            CIStatus.FAILURE: "✗",
            CIStatus.NONE: "–",
        }[self]


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    author: str
    is_draft: bool
    created_at: datetime
    updated_at: datetime
    ci: CIStatus

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def style(self) -> str:
        """Rich style used for every cell of this pull request's row."""
        if self.is_draft:
            return "grey50"
        return {
            CIStatus.SUCCESS: "green",
            CIStatus.PENDING: "yellow",
            CIStatus.FAILURE: "red",
            CIStatus.NONE: "white",
        }[self.ci]

    @property
    def state_label(self) -> str:
        if self.is_draft:
            return "draft"
        return self.ci.value


def humanise_age(moment: datetime, *, now: datetime | None = None) -> str:
    """Render a timestamp as a compact relative age, e.g. ``3d`` or ``12m``."""
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 365:
        return f"{days}d"
    return f"{days // 365}y"
