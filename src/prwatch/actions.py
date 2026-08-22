"""Run an external agent CLI (claude, copilot, ...) against a pull request."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable

from .config import Config
from .models import PullRequest

PROMPT_PLACEHOLDER = "{prompt}"


class ActionError(Exception):
    """Raised when the agent command cannot be built or started."""


def render_prompt(cfg: Config, action: str, pr: PullRequest) -> str:
    """Fill the configured prompt template with this pull request's fields."""
    template = cfg.prompt_template(action)
    try:
        return template.format(
            url=pr.url,
            repo=pr.repo,
            number=pr.number,
            title=pr.title,
            author=pr.author,
            state=pr.state_label,
        )
    except (KeyError, IndexError) as exc:
        raise ActionError(f"prompts.{action} uses an unknown placeholder: {exc}") from exc


def build_command(cfg: Config, agent: str, prompt: str) -> list[str]:
    """Substitute the prompt into the agent's command template."""
    template = cfg.agent_command(agent)
    if any(PROMPT_PLACEHOLDER in arg for arg in template):
        command = [arg.replace(PROMPT_PLACEHOLDER, prompt) for arg in template]
    else:
        command = [*template, prompt]
    if not shutil.which(command[0]):
        raise ActionError(f"{command[0]!r} is not on PATH")
    return command


async def stream_command(
    command: list[str], on_line: Callable[[str], None]
) -> int:
    """Run ``command``, passing each output line to ``on_line``; return the exit code.

    stderr is merged into stdout so the caller sees one ordered stream. If the
    awaiting task is cancelled the child process is killed.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (OSError, ValueError) as exc:
        raise ActionError(f"could not start {command[0]!r}: {exc}") from exc

    assert process.stdout is not None
    try:
        while True:
            try:
                raw = await process.stdout.readline()
            except ValueError:  # line longer than the stream limit
                on_line("[prwatch] dropped an over-long output line")
                continue
            if not raw:
                break
            on_line(raw.decode("utf-8", "replace").rstrip("\n"))
        return await process.wait()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
