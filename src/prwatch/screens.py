"""Modal screens: repo management, agent picking, and agent output."""

from __future__ import annotations

import shlex

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog, Static

from .actions import ActionError, stream_command

MAX_ECHOED_ARG = 100


class AddRepoScreen(ModalScreen[str | None]):
    """Ask for a repository reference; returns the raw text or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Add repository", classes="title")
            yield Input(placeholder="owner/name or a GitHub URL", id="repo-input")
            yield Static("enter add · esc cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no confirmation."""

    BINDINGS = [
        Binding("escape,n", "answer(False)", "No"),
        Binding("y", "answer(True)", "Yes"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._question, classes="title")
            with Horizontal(id="buttons"):
                yield Button("No", id="no")
                yield Button("Yes", variant="error", id="yes")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


class ReposScreen(ModalScreen[bool]):
    """List the configured repos and add/remove them. Returns True if changed."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("a", "add", "Add"),
        Binding("d,delete,backspace", "remove", "Remove"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._changed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Repositories", classes="title")
            yield ListView(id="repo-list")
            yield Static("a add · d remove · esc close", classes="hint")

    async def on_mount(self) -> None:
        await self._reload()

    async def _reload(self) -> None:
        listing = self.query_one("#repo-list", ListView)
        index = listing.index
        await listing.clear()
        repos = self.app.cfg.repos
        if repos:
            listing.extend([ListItem(Label(repo)) for repo in repos])
            listing.index = min(index or 0, len(repos) - 1)
        else:
            listing.append(ListItem(Label("(none configured — press 'a')")))
            listing.index = None
        listing.focus()

    def _selected_repo(self) -> str | None:
        index = self.query_one("#repo-list", ListView).index
        repos = self.app.cfg.repos
        if index is None or not repos or index >= len(repos):
            return None
        return repos[index]

    @work
    async def action_add(self) -> None:
        value = await self.app.push_screen_wait(AddRepoScreen())
        if value and self.app.add_repo(value):
            self._changed = True
            await self._reload()

    @work
    async def action_remove(self) -> None:
        repo = self._selected_repo()
        if repo is None:
            return
        confirmed = await self.app.push_screen_wait(ConfirmScreen(f"Stop watching {repo}?"))
        if confirmed and self.app.remove_repo(repo):
            self._changed = True
            await self._reload()

    def action_close(self) -> None:
        self.dismiss(self._changed)


class AgentPickerScreen(ModalScreen[str | None]):
    """Choose which configured agent CLI should run the action."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, agents: list[str], default: str, action: str) -> None:
        super().__init__()
        self._agents = agents
        self._default = default
        self._action = action

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"Run {self._action} with", classes="title")
            yield ListView(
                *[ListItem(Label(agent)) for agent in self._agents], id="agent-list"
            )
            yield Static("enter run · esc cancel", classes="hint")

    def on_mount(self) -> None:
        listing = self.query_one(ListView)
        if self._default in self._agents:
            listing.index = self._agents.index(self._default)
        listing.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None:
            self.dismiss(self._agents[index])

    def action_cancel(self) -> None:
        self.dismiss(None)


class OutputScreen(ModalScreen[None]):
    """Stream an agent CLI's output into a scrollback pane."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, command: list[str]) -> None:
        super().__init__()
        self._title = title
        self._command = command
        self._worker = None

    def compose(self) -> ComposeResult:
        with Vertical(id="output-dialog"):
            yield Static(self._title, id="output-title")
            yield RichLog(
                id="output-log", wrap=True, markup=False, highlight=False, max_lines=5000
            )
            yield Static("running… · esc to stop and close", id="output-hint")

    def on_mount(self) -> None:
        self._worker = self._run()

    @work(exit_on_error=False)
    async def _run(self) -> None:
        log = self.query_one(RichLog)
        hint = self.query_one("#output-hint", Static)
        log.write(Text(f"$ {_echo(self._command)}", style="dim"))
        try:
            code = await stream_command(self._command, log.write)
        except ActionError as exc:
            log.write(Text(str(exc), style="bold red"))
            hint.update("failed to start · esc to close")
            return
        style = "green" if code == 0 else "bold red"
        log.write(Text(f"[exit {code}]", style=style))
        hint.update(f"finished (exit {code}) · esc to close")

    def action_close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self.dismiss(None)


def _echo(command: list[str]) -> str:
    """Render the command for display, eliding very long arguments."""
    shown = [
        arg if len(arg) <= MAX_ECHOED_ARG else arg[: MAX_ECHOED_ARG - 1] + "…"
        for arg in command
    ]
    return shlex.join(shown)


class HelpScreen(ModalScreen[None]):
    """Key bindings and the colour legend."""

    BINDINGS = [Binding("escape,question_mark,q", "dismiss_help", "Close")]

    ROWS = [
        ("r", "refresh now"),
        ("a", "add a repository"),
        ("m", "manage repositories"),
        ("s", "summarise the selected PR with an agent"),
        ("v", "review the selected PR with an agent"),
        ("enter", "open the PR in a browser"),
        ("y", "copy the PR URL"),
        ("/", "filter by repo, title, author or number"),
        ("o / O", "cycle sort column / reverse"),
        ("d", "toggle showing drafts"),
        ("q", "quit"),
    ]

    LEGEND = [
        ("green", "passed / no checks"),
        ("yellow", "pending"),
        ("red", "failed"),
        ("grey50", "draft"),
    ]

    def compose(self) -> ComposeResult:
        body = Text()
        for key, description in self.ROWS:
            body.append(f"{key:>7}", style="bold cyan")
            body.append(f"  {description}\n")
        body.append("\n")
        for index, (style, description) in enumerate(self.LEGEND):
            body.append("  " if index else " " * 7)
            body.append(f"■ {description}", style=style)
        with Vertical(id="dialog"):
            yield Static("prwatch keys", classes="title")
            yield Static(body, id="help-table")
            yield Static("esc close", classes="hint")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
