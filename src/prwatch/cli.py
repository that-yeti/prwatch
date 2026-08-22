"""Command line entry point: launch the TUI, or manage repos non-interactively."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, config_path, load_config, save_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prwatch",
        description="Watch open pull requests across a set of GitHub repositories.",
    )
    parser.add_argument("-c", "--config", help="path to the config file")
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add", help="watch one or more repositories")
    add.add_argument("repos", nargs="+", metavar="owner/name")

    remove = sub.add_parser("remove", aliases=["rm"], help="stop watching repositories")
    remove.add_argument("repos", nargs="+", metavar="owner/name")

    sub.add_parser("list", aliases=["ls"], help="list the watched repositories")
    sub.add_parser("config", help="print the config file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "config":
        print(config_path(args.config))
        return 0

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"prwatch: {exc}", file=sys.stderr)
        return 1

    match args.command:
        case "add" | "remove" | "rm":
            changed = False
            for repo in args.repos:
                try:
                    cfg = cfg.with_repo(repo) if args.command == "add" else cfg.without_repo(repo)
                except ConfigError as exc:
                    print(f"prwatch: {exc}", file=sys.stderr)
                    continue
                changed = True
            if changed:
                path = save_config(cfg)
                print(f"wrote {path}")
            for repo in cfg.repos:
                print(repo)
            return 0
        case "list" | "ls":
            for repo in cfg.repos:
                print(repo)
            if not cfg.repos:
                print(f"no repositories configured in {config_path(args.config)}")
            return 0

    if cfg.path is not None and not cfg.path.exists():
        save_config(cfg)

    from .app import PRWatchApp

    PRWatchApp(cfg).run()
    return 0
