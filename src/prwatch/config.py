"""Loading, validating and writing the prwatch config file."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.9/3.10 only
    import tomli as tomllib

DEFAULT_API_URL = "https://api.github.com/graphql"

REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

DEFAULT_AGENTS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "{prompt}"],
    "copilot": ["copilot", "-p", "{prompt}", "--allow-all-tools"],
}

DEFAULT_PROMPTS: dict[str, str] = {
    "summary": (
        "Summarise the GitHub pull request {url} ({repo} #{number}: {title}). "
        "Use the gh CLI to read the PR description, the diff and the CI status. "
        "Cover what the change does, why, which files matter most, and anything "
        "that looks unfinished. Keep it under 20 lines of plain text."
    ),
    "review": (
        "Review the GitHub pull request {url} ({repo} #{number}: {title}). "
        "Use the gh CLI to read the PR description and the full diff. "
        "Report correctness bugs, missing test coverage and risky changes, most "
        "important first, quoting file:line for each finding. Say so plainly if "
        "you find nothing serious."
    ),
}


class ConfigError(Exception):
    """Raised when the config file is present but unusable."""


@dataclass
class Config:
    repos: list[str] = field(default_factory=list)
    refresh_seconds: int = 300
    default_agent: str = "claude"
    api_url: str = DEFAULT_API_URL
    token_env: str | None = None
    max_prs_per_repo: int = 50
    agents: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_AGENTS))
    prompts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_PROMPTS))
    path: Path | None = None

    def with_repo(self, repo: str) -> Config:
        """Return a copy with ``repo`` added (case-insensitively de-duplicated)."""
        repo = normalise_repo(repo)
        if repo.lower() in {existing.lower() for existing in self.repos}:
            raise ConfigError(f"{repo} is already configured")
        return replace(self, repos=sorted([*self.repos, repo], key=str.lower))

    def without_repo(self, repo: str) -> Config:
        """Return a copy with ``repo`` removed."""
        repo = normalise_repo(repo)
        remaining = [r for r in self.repos if r.lower() != repo.lower()]
        if len(remaining) == len(self.repos):
            raise ConfigError(f"{repo} is not configured")
        return replace(self, repos=remaining)

    def agent_command(self, agent: str) -> list[str]:
        try:
            return self.agents[agent]
        except KeyError:
            raise ConfigError(f"unknown agent {agent!r}") from None

    def prompt_template(self, action: str) -> str:
        try:
            return self.prompts[action]
        except KeyError:
            raise ConfigError(f"unknown action {action!r}") from None


def normalise_repo(repo: str) -> str:
    """Accept ``owner/name``, a GitHub URL, or ``git@github.com:owner/name.git``."""
    repo = repo.strip()
    if not repo:
        raise ConfigError("repository name is empty")
    if "github.com" in repo:
        tail = re.split(r"github\.com[:/]", repo, maxsplit=1)[-1]
        parts = [p for p in tail.split("/") if p][:2]
        repo = "/".join(parts)
    repo = repo.removesuffix(".git").strip("/")
    if not REPO_RE.match(repo):
        raise ConfigError(f"{repo!r} is not an owner/name repository reference")
    return repo


def config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the config file location (``$PRWATCH_CONFIG`` wins, then XDG)."""
    if explicit is not None:
        return Path(explicit).expanduser()
    from_env = os.environ.get("PRWATCH_CONFIG")
    if from_env:
        return Path(from_env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "prwatch" / "config.toml"


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """Read the config file, falling back to defaults when it does not exist."""
    path = config_path(explicit)
    if not path.exists():
        return Config(path=path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    cfg = Config(path=path)
    cfg.repos = [normalise_repo(r) for r in _string_list(raw, "repos")]
    cfg.refresh_seconds = _positive_int(raw, "refresh_seconds", cfg.refresh_seconds)
    cfg.max_prs_per_repo = min(100, _positive_int(raw, "max_prs_per_repo", cfg.max_prs_per_repo))
    cfg.api_url = _string(raw, "api_url", cfg.api_url)
    if "token" in raw:
        raise ConfigError(
            f"{path}: tokens are no longer read from the config file - "
            "delete the token line and export $GITHUB_TOKEN instead "
            '(or set token_env = "MY_VAR" to name a different variable)'
        )
    if "token_env" in raw:
        cfg.token_env = _string(raw, "token_env", "")

    agents = dict(DEFAULT_AGENTS)
    for name, spec in (raw.get("agents") or {}).items():
        command = spec.get("command") if isinstance(spec, dict) else spec
        if not isinstance(command, list) or not all(isinstance(a, str) for a in command) or not command:
            raise ConfigError(f"agents.{name}.command must be a non-empty list of strings")
        agents[name] = command
    cfg.agents = agents

    prompts = dict(DEFAULT_PROMPTS)
    for name, template in (raw.get("prompts") or {}).items():
        if not isinstance(template, str):
            raise ConfigError(f"prompts.{name} must be a string")
        prompts[name] = template
    cfg.prompts = prompts

    cfg.default_agent = _string(raw, "default_agent", cfg.default_agent)
    if cfg.default_agent not in cfg.agents:
        raise ConfigError(f"default_agent {cfg.default_agent!r} has no [agents.{cfg.default_agent}] entry")
    return cfg


def save_config(cfg: Config, explicit: str | os.PathLike[str] | None = None) -> Path:
    """Write the config back out, atomically. Comments are not preserved.

    The file holds no secrets (the token comes from the environment), but it is
    still created 0600 -- it lists which repositories you watch.
    """
    path = Path(explicit).expanduser() if explicit is not None else (cfg.path or config_path())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(_dumps(cfg))
    tmp.replace(path)
    cfg.path = path
    return path


def _string_list(raw: dict, key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return value


def _string(raw: dict, key: str, fallback: str) -> str:
    value = raw.get(key, fallback)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _positive_int(raw: dict, key: str, fallback: int) -> int:
    value = raw.get(key, fallback)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{key} must be a positive integer")
    return value


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _toml_str(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _toml_str(key)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def _dumps(cfg: Config) -> str:
    lines = [
        "# prwatch configuration -- rewritten by the app, comments are not preserved.",
        "",
        "repos = [",
        *[f"    {_toml_str(repo)}," for repo in cfg.repos],
        "]",
        "",
        f"refresh_seconds = {cfg.refresh_seconds}",
        f"max_prs_per_repo = {cfg.max_prs_per_repo}",
        f"default_agent = {_toml_str(cfg.default_agent)}",
        f"api_url = {_toml_str(cfg.api_url)}",
    ]
    if cfg.token_env:
        lines.append(f"token_env = {_toml_str(cfg.token_env)}")
    for name, command in cfg.agents.items():
        lines += ["", f"[agents.{_toml_key(name)}]", f"command = {_toml_array(command)}"]
    lines += ["", "[prompts]"]
    for name, template in cfg.prompts.items():
        lines.append(f"{_toml_key(name)} = {_toml_str(template)}")
    return "\n".join(lines) + "\n"
