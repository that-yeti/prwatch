"""The prwatch Textual application."""

from __future__ import annotations

import webbrowser
from datetime import UTC, datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input, Static

from .actions import ActionError, build_command, render_prompt
from .config import Config, ConfigError, normalise_repo, save_config
from .github import FetchResult, GitHubError, fetch_pull_requests
from .models import CIStatus, PullRequest, humanise_age
from .screens import (
    AddRepoScreen,
    AgentPickerScreen,
    HelpScreen,
    OutputScreen,
    ReposScreen,
)

SORT_FIELDS: list[tuple[str, str]] = [
    ("updated", "last updated"),
    ("created", "created"),
    ("repo", "repo"),
    ("number", "PR number"),
    ("title", "title"),
    ("ci", "CI state"),
]

_CI_ORDER = {
    CIStatus.FAILURE: 0,
    CIStatus.PENDING: 1,
    CIStatus.NONE: 2,
    CIStatus.SUCCESS: 3,
}


class PRWatchApp(App[None]):
    """Dashboard of open pull requests across the configured repositories."""

    CSS_PATH = "app.tcss"
    TITLE = "prwatch"

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("s", "agent_action('summary')", "Summary"),
        Binding("v", "agent_action('review')", "Review"),
        Binding("enter", "open_pr", "Open"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("a", "add_repo", "Add repo"),
        Binding("m", "manage_repos", "Repos"),
        Binding("slash", "start_filter", "Filter"),
        Binding("o", "cycle_sort", "Sort"),
        Binding("O", "reverse_sort", "Reverse", show=False),
        Binding("d", "toggle_drafts", "Drafts", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("escape", "clear_filter", "", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self._prs: list[PullRequest] = []
        self._visible: list[PullRequest] = []
        self._warnings: list[str] = []
        self._error: str | None = None
        self._last_refresh: datetime | None = None
        self._filter = ""
        self._sort_index = 0
        self._sort_reverse = True
        self._show_drafts = True
        self._refresh_timer = None

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="filter: repo, title, author or #number", id="filter")
        yield DataTable(id="prs", cursor_type="row", zebra_stripes=False)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#prs", DataTable)
        table.add_column("", key="ci", width=1)
        table.add_column("Repo", key="repo")
        table.add_column("PR", key="number", width=6)
        table.add_column("Title", key="title")
        table.add_column("Created", key="created", width=8)
        table.add_column("Updated", key="updated", width=8)
        table.focus()
        self._render()
        self.action_refresh()
        self._refresh_timer = self.set_interval(
            self.cfg.refresh_seconds, self.action_refresh
        )

    # ------------------------------------------------------------ data fetch

    def action_refresh(self) -> None:
        if not self.cfg.repos:
            self._prs = []
            self._error = "no repositories configured - press 'a' to add one"
            self._render()
            return
        self._fetch()

    @work(exclusive=True, group="fetch", exit_on_error=False)
    async def _fetch(self) -> None:
        self._set_status("refreshing…")
        try:
            result: FetchResult = await fetch_pull_requests(self.cfg)
        except GitHubError as exc:
            self._error = str(exc)
        else:
            self._error = None
            self._prs = result.pull_requests
            self._warnings = result.warnings
            self._last_refresh = datetime.now(UTC)
        self._render()

    # ------------------------------------------------------------- rendering

    def _matches_filter(self, pr: PullRequest) -> bool:
        if not self._show_drafts and pr.is_draft:
            return False
        if not self._filter:
            return True
        needle = self._filter.lower()
        haystack = f"{pr.repo} #{pr.number} {pr.title} {pr.author} {pr.state_label}"
        return needle in haystack.lower()

    def _sort_key(self, pr: PullRequest):
        field = SORT_FIELDS[self._sort_index][0]
        match field:
            case "updated":
                return pr.updated_at
            case "created":
                return pr.created_at
            case "repo":
                return (pr.repo.lower(), pr.number)
            case "number":
                return (pr.number, pr.repo.lower())
            case "title":
                return pr.title.lower()
            case _:
                return (_CI_ORDER[pr.ci], pr.is_draft, pr.updated_at)

    def _render(self) -> None:
        table = self.query_one("#prs", DataTable)
        previous = self._selected_pr()
        self._visible = sorted(
            (pr for pr in self._prs if self._matches_filter(pr)),
            key=self._sort_key,
            reverse=self._sort_reverse,
        )
        table.clear()
        now = datetime.now(UTC)
        for pr in self._visible:
            style = pr.style
            table.add_row(
                Text(pr.ci.symbol, style=style),
                Text(pr.repo, style=style),
                Text(f"#{pr.number}", style=style, justify="right"),
                Text(pr.title, style=style, no_wrap=True, overflow="ellipsis"),
                Text(humanise_age(pr.created_at, now=now), style=style),
                Text(humanise_age(pr.updated_at, now=now), style=style),
                key=pr.key,
            )
        if previous is not None:
            for row, pr in enumerate(self._visible):
                if pr.key == previous.key:
                    table.move_cursor(row=row)
                    break
        self._render_status()

    def _render_status(self) -> None:
        status = self.query_one("#status", Static)
        status.set_class(bool(self._error), "error")
        if self._error:
            status.update(Text(f"error: {self._error}", style="bold"))
            return

        counts = {state: 0 for state in CIStatus}
        drafts = 0
        for pr in self._prs:
            if pr.is_draft:
                drafts += 1
            counts[pr.ci] += 1
        stamp = (
            self._last_refresh.astimezone().strftime("%H:%M:%S")
            if self._last_refresh
            else "never"
        )
        parts = [
            f"{len(self._visible)}/{len(self._prs)} PRs",
            f"{len(self.cfg.repos)} repos",
            (
                f"✓{counts[CIStatus.SUCCESS]} •{counts[CIStatus.PENDING]} "
                f"✗{counts[CIStatus.FAILURE]} –{counts[CIStatus.NONE]} · {drafts} draft"
            ),
            f"sort {SORT_FIELDS[self._sort_index][1]}{' ↓' if self._sort_reverse else ' ↑'}",
            f"updated {stamp}",
        ]
        if self._filter:
            parts.insert(1, f"filter {self._filter!r}")
        if not self._show_drafts:
            parts.insert(1, "drafts hidden")
        if self._warnings:
            parts.append(f"{len(self._warnings)} repo warning(s): {self._warnings[0]}")
        status.update(" · ".join(parts))

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _selected_pr(self) -> PullRequest | None:
        table = self.query_one("#prs", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._visible):
            return self._visible[row]
        return None

    # --------------------------------------------------------------- actions

    def action_cycle_sort(self) -> None:
        self._sort_index = (self._sort_index + 1) % len(SORT_FIELDS)
        self._render()

    def action_reverse_sort(self) -> None:
        self._sort_reverse = not self._sort_reverse
        self._render()

    def action_toggle_drafts(self) -> None:
        self._show_drafts = not self._show_drafts
        self._render()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        names = [name for name, _ in SORT_FIELDS]
        column = str(event.column_key.value)
        if column not in names:
            return
        index = names.index(column)
        if index == self._sort_index:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_index = index
            self._sort_reverse = column in {"updated", "created"}
        self._render()

    def action_open_pr(self) -> None:
        pr = self._selected_pr()
        if pr is None:
            return
        self._open_url(pr.url)

    @work(thread=True, exit_on_error=False)
    def _open_url(self, url: str) -> None:
        webbrowser.open(url)

    def action_copy_url(self) -> None:
        pr = self._selected_pr()
        if pr is None:
            return
        self.copy_to_clipboard(pr.url)
        self.notify(f"copied {pr.url}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_pr()

    # ------------------------------------------------------------ filtering

    def action_start_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.add_class("visible")
        field.focus()

    def action_clear_filter(self) -> None:
        field = self.query_one("#filter", Input)
        field.value = ""
        field.remove_class("visible")
        self._filter = ""
        self.query_one("#prs", DataTable).focus()
        self._render()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._filter = event.value.strip()
            self._render()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one("#prs", DataTable).focus()

    # ------------------------------------------------------ repo management

    def add_repo(self, value: str) -> bool:
        """Add a repo to the config and persist it. Returns True on success."""
        try:
            repo = normalise_repo(value)
            cfg = self.cfg.with_repo(repo)
            save_config(cfg)
        except (ConfigError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return False
        self.cfg = cfg
        self.notify(f"added {repo}")
        return True

    def remove_repo(self, repo: str) -> bool:
        """Remove a repo from the config and persist it. Returns True on success."""
        try:
            cfg = self.cfg.without_repo(repo)
            save_config(cfg)
        except (ConfigError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return False
        self.cfg = cfg
        self._prs = [pr for pr in self._prs if pr.repo.lower() != repo.lower()]
        self.notify(f"removed {repo}")
        return True

    @work
    async def action_add_repo(self) -> None:
        value = await self.push_screen_wait(AddRepoScreen())
        if value and self.add_repo(value):
            self.action_refresh()

    @work
    async def action_manage_repos(self) -> None:
        changed = await self.push_screen_wait(ReposScreen())
        if changed:
            self.action_refresh()

    # ---------------------------------------------------------- agent actions

    @work
    async def action_agent_action(self, action: str) -> None:
        pr = self._selected_pr()
        if pr is None:
            self.notify("no pull request selected", severity="warning")
            return
        agents = sorted(self.cfg.agents)
        if len(agents) == 1:
            agent = agents[0]
        else:
            agent = await self.push_screen_wait(
                AgentPickerScreen(agents, self.cfg.default_agent, action)
            )
        if agent is None:
            return
        try:
            prompt = render_prompt(self.cfg, action, pr)
            command = build_command(self.cfg, agent, prompt)
        except (ActionError, ConfigError) as exc:
            self.notify(str(exc), severity="error")
            return
        title = f"{agent} {action} — {pr.repo} #{pr.number}: {pr.title}"
        await self.push_screen_wait(OutputScreen(title, command))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())
