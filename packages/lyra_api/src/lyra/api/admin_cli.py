"""Inspect and operate a running Lyra server from the command line."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from lyra.api import LyraAdminClient, LyraAPIError
from pydantic import ValidationError
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from lyra.sdk.types import JsonValue
    from pydantic import BaseModel


class UsageError(ValueError):
    """Invalid command arguments or missing connection credentials."""


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


@dataclass(frozen=True)
class _Command:
    name: str
    description: str
    run: Callable[[LyraAdminClient, argparse.Namespace], BaseModel]
    mutates: bool = False


_COMMANDS = (
    _Command(
        "health",
        "Check readiness, or process liveness with --live.",
        lambda c, a: c.health.liveness() if a.live else c.health.readiness(),
    ),
    _Command(
        "status", "Inspect administrative service status.", lambda c, _: c.status()
    ),
    _Command(
        "config-summary",
        "Show effective configuration without secrets.",
        lambda c, _: c.config_summary(),
    ),
    _Command(
        "jobs list",
        "List retained jobs.",
        lambda c, a: c.jobs.list(limit=a.limit, status=a.status, metric=a.metric),
    ),
    _Command(
        "jobs cancel",
        "Request cancellation of a retained job.",
        lambda c, a: c.jobs.cancel(a.id),
        mutates=True,
    ),
    _Command(
        "workers list",
        "List workers and inspection freshness.",
        lambda c, _: c.workers.list(),
    ),
    _Command(
        "workers get",
        "Inspect one worker and its tasks.",
        lambda c, a: c.workers.get(a.name),
    ),
    _Command(
        "workers restart",
        "Request restart of all workers.",
        lambda c, a: c.workers.restart(timeout=a.restart_timeout),
        mutates=True,
    ),
    _Command(
        "queues list",
        "Inspect queues and worker coverage.",
        lambda c, _: c.queues.list(),
    ),
    _Command(
        "repos list", "List plugin repositories.", lambda c, _: c.plugin_repos.list()
    ),
    _Command(
        "repos add",
        "Register a repository and refresh the catalog.",
        lambda c, a: c.plugin_repos.create(
            a.source, repo_id=a.id, enabled=not a.disabled
        ),
        mutates=True,
    ),
    _Command(
        "repos update",
        "Update a repository source and refresh the catalog.",
        lambda c, a: c.plugin_repos.update(a.id, source=a.source),
        mutates=True,
    ),
    _Command(
        "repos enable",
        "Enable a repository and refresh the catalog.",
        lambda c, a: c.plugin_repos.update(a.id, enabled=True),
        mutates=True,
    ),
    _Command(
        "repos disable",
        "Disable a repository and refresh the catalog.",
        lambda c, a: c.plugin_repos.update(a.id, enabled=False),
        mutates=True,
    ),
    _Command(
        "repos delete",
        "Delete a repository and refresh the catalog.",
        lambda c, a: c.plugin_repos.delete(a.id),
        mutates=True,
    ),
    _Command(
        "repos sync",
        "Synchronize a repository and refresh the catalog.",
        lambda c, a: c.plugin_repos.sync(a.id),
        mutates=True,
    ),
    _Command(
        "catalog show",
        "Show catalog contents, sources, and routing.",
        lambda c, _: c.catalog.summary(),
    ),
    _Command(
        "catalog refresh",
        "Synchronize enabled sources and refresh the catalog.",
        lambda c, _: c.catalog.refresh(),
        mutates=True,
    ),
    _Command(
        "routing list",
        "List routing assignments and allowed queues.",
        lambda c, _: c.routing.list(),
    ),
    _Command(
        "routing set",
        "Assign a metric to a queue.",
        lambda c, a: c.routing.set(a.metric, a.queue),
        mutates=True,
    ),
    _Command(
        "routing delete",
        "Remove a metric's explicit routing assignment.",
        lambda c, a: c.routing.delete(a.metric),
        mutates=True,
    ),
)


def _nonempty(value: str) -> str:
    if not value.strip():
        message = "must not be empty"
        raise argparse.ArgumentTypeError(message)
    return value.strip()


def _host(value: str) -> str:
    value = _nonempty(value).rstrip("/")
    try:
        parsed = urlsplit(f"//{value}")
        valid = parsed.hostname and (parsed.port is None or parsed.port > 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if (
        not valid
        or "://" in value
        or parsed.username is not None
        or any((parsed.query, parsed.fragment))
        or any(c.isspace() for c in value)
    ):
        message = (
            "use HOST[:PORT][/BASE_PATH] without credentials or scheme; "
            "select HTTPS with --secure"
        )
        raise argparse.ArgumentTypeError(message)
    return value


def _nonnegative(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        message = "must be a number"
        raise argparse.ArgumentTypeError(message) from exc
    if not math.isfinite(number) or number < 0:
        message = "must be a finite number greater than or equal to zero"
        raise argparse.ArgumentTypeError(message)
    return number


def _positive(value: str) -> float:
    number = _nonnegative(value)
    if number == 0:
        message = "must be greater than zero"
        raise argparse.ArgumentTypeError(message)
    return number


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        message = "must be an integer"
        raise argparse.ArgumentTypeError(message) from exc
    if not 1 <= number <= 100:
        message = "must be between 1 and 100"
        raise argparse.ArgumentTypeError(message)
    return number


def _command_arguments(parser: argparse.ArgumentParser, command: _Command) -> None:
    if command.mutates:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm this mutation without prompting.",
        )
    if command.name.startswith("repos "):
        _repo_arguments(parser, command.name)
        return
    match command.name:
        case "health":
            parser.add_argument(
                "--live",
                action="store_true",
                help="Check process liveness instead of readiness.",
            )
        case "jobs list":
            parser.add_argument(
                "--limit",
                type=_limit,
                default=50,
                help="Maximum retained jobs (1-100; default: 50).",
            )
            parser.add_argument(
                "--status",
                choices=("queued", "running", "succeeded", "failed", "cancelled"),
            )
            parser.add_argument("--metric", type=_nonempty)
        case "workers get":
            parser.add_argument("name", type=_nonempty)
        case "workers restart":
            parser.add_argument(
                "--restart-timeout",
                type=_nonnegative,
                default=30.0,
                help="Worker drain timeout in seconds (default: 30).",
            )
        case "jobs cancel":
            parser.add_argument("id", type=_nonempty)
        case "routing set" | "routing delete":
            parser.add_argument("metric", type=_nonempty)
            if command.name == "routing set":
                parser.add_argument("queue", type=_nonempty)


def _repo_arguments(parser: argparse.ArgumentParser, name: str) -> None:
    match name:
        case "repos add":
            parser.add_argument("source", type=_nonempty)
            parser.add_argument("--id", type=_nonempty)
            parser.add_argument("--disabled", action="store_true")
        case "repos update":
            parser.add_argument("id", type=_nonempty)
            parser.add_argument("--source", required=True, type=_nonempty)
        case "repos enable" | "repos disable" | "repos delete" | "repos sync":
            parser.add_argument("id", type=_nonempty)


def build_parsers() -> list[argparse.ArgumentParser]:
    """Build root, group, and leaf parsers for execution and documentation.

    Returns:
        All parsers in command order, with the root first.
    """
    parser = _Parser(
        prog="lyra-admin",
        allow_abbrev=False,
        description="Inspect and operate an existing Lyra server.",
        epilog=(
            "Put connection options and --json before the command. "
            "Example: lyra-admin --host localhost:5219 --no-secure health"
        ),
    )
    parser.add_argument(
        "--host",
        type=_host,
        default="localhost:5219",
        help="HOST[:PORT][/BASE_PATH] without a scheme (default: localhost:5219).",
    )
    parser.add_argument(
        "--timeout",
        type=_positive,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--admin-api-key",
        type=_nonempty,
        help="Admin token; defaults to LYRA_ADMIN_API_KEY.",
    )
    schemes = parser.add_mutually_exclusive_group()
    schemes.add_argument(
        "--secure", dest="secure", action="store_true", help="Use HTTPS."
    )
    schemes.add_argument(
        "--no-secure", dest="secure", action="store_false", help="Use HTTP (default)."
    )
    parser.set_defaults(secure=False)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the API response as JSON; mutations require --yes.",
    )
    roots = parser.add_subparsers(required=True)
    parsers: list[argparse.ArgumentParser] = [parser]
    groups: dict[str, argparse.ArgumentParser] = {}
    for command in _COMMANDS:
        group, separator, name = command.name.partition(" ")
        if not separator:
            leaf = roots.add_parser(
                group,
                help=command.description,
                description=command.description,
                allow_abbrev=False,
            )
        else:
            if group not in groups:
                group_parser = roots.add_parser(
                    group, help=f"Manage {group}.", allow_abbrev=False
                )
                parsers.append(group_parser)
                groups[group] = group_parser
            # Each group is populated together to avoid storing argparse internals.
            continue
        leaf.set_defaults(command=command)
        _command_arguments(leaf, command)
        parsers.append(leaf)
    for group, group_parser in groups.items():
        children = group_parser.add_subparsers(required=True)
        for command in _COMMANDS:
            prefix, separator, name = command.name.partition(" ")
            if separator and prefix == group:
                leaf = children.add_parser(
                    name,
                    help=command.description,
                    description=command.description,
                    allow_abbrev=False,
                )
                leaf.set_defaults(command=command)
                _command_arguments(leaf, command)
                parsers.append(leaf)
    return parsers


def build_parser() -> argparse.ArgumentParser:
    """Build the executable command parser.

    Returns:
        The root parser with all supported subcommands.
    """
    return build_parsers()[0]


def _text(value: JsonValue) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return json.dumps(value, ensure_ascii=False)


def _render(value: JsonValue, *, indent: str = "") -> list[str]:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            label = key.replace("_", " ").capitalize()
            if isinstance(child, (dict, list)) and child:
                lines.append(f"{indent}{label}:")
                lines.extend(_render(child, indent=f"{indent}  "))
            else:
                text = "(none)" if child in ([], {}) else _text(child)
                lines.append(f"{indent}{label}: {text}")
        return lines or [f"{indent}(none)"]
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value):
            rows = [row for row in value if isinstance(row, dict)]
            keys = list(dict.fromkeys(key for row in rows for key in row))
            table = [[key.replace("_", " ").upper() for key in keys]]
            table.extend([_text(row.get(key)) for key in keys] for row in rows)
            widths = [
                max(len(row[index]) for row in table) for index in range(len(keys))
            ]
            return [
                indent
                + "  ".join(
                    cell.ljust(width) for cell, width in zip(row, widths, strict=True)
                ).rstrip()
                for row in table
            ]
        return [line for child in value for line in _render(child, indent=indent)] or [
            f"{indent}(none)"
        ]
    return [f"{indent}{_text(value)}"]


def _diagnostic(kind: str, message: str, *, json_output: bool) -> None:
    output = (
        json.dumps({"error": {"kind": kind, "message": message}})
        if json_output
        else f"{kind}: {message}"
    )
    sys.stderr.write(f"{output}\n")


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if args.json or not sys.stdin.isatty() or not sys.stderr.isatty():
        return False
    target = " ".join(
        f"{field}={value}"
        for field in ("id", "metric", "queue", "source", "restart_timeout")
        if (value := getattr(args, field, None)) is not None
    )
    sys.stderr.write(
        f"{args.command.description} Target: {args.host} {target}. Continue? [y/N] "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _operation_error(command: _Command, data: dict[str, JsonValue]) -> str | None:
    if command.name == "health" and data.get("status") == "not_ready":
        return "Service dependencies are not ready."
    refresh = data.get("catalog_refresh")
    if isinstance(refresh, dict) and refresh.get("refreshed") is False:
        return (
            "Repository state may have changed, but catalog refresh failed: "
            f"{refresh.get('error') or 'unknown error'}."
        )
    if command.name == "workers restart" and data.get("requested") is False:
        return "Worker restart was not requested."
    if command.name == "jobs cancel" and data.get("cancellation_requested") is False:
        return "Job cancellation was not requested."
    return None


def _execute(arguments: Sequence[str], *, json_output: bool) -> int:
    args = build_parser().parse_args(arguments)
    key = args.admin_api_key or os.getenv("LYRA_ADMIN_API_KEY")
    if args.command.name != "health" and (key is None or not key.strip()):
        message = "Set LYRA_ADMIN_API_KEY or supply --admin-api-key."
        raise UsageError(message)
    if args.command.mutates and not _confirmed(args):
        _diagnostic(
            "confirmation",
            "No change requested. Confirm interactively or supply --yes.",
            json_output=json_output,
        )
        return 3
    client = LyraAdminClient(
        args.host, timeout=args.timeout, admin_api_key=key, secure=args.secure
    )
    response = args.command.run(client, args)
    data = response.model_dump(mode="json")
    output = (
        json.dumps(data, ensure_ascii=False)
        if json_output
        else "\n".join(_render(data))
    )
    sys.stdout.write(f"{output}\n")
    error = _operation_error(args.command, data)
    if error is not None:
        _diagnostic("operation", error, json_output=json_output)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one administrative command.

    Returns:
        Zero on success, 1 on operational failure, 2 on usage errors, 3 on
        missing confirmation, or 130 when interrupted.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        return _execute(arguments, json_output=json_output)
    except UsageError as exc:
        _diagnostic("usage", str(exc), json_output=json_output)
        return 2
    except (LyraAPIError, ValidationError, json.JSONDecodeError) as exc:
        _diagnostic("request", str(exc), json_output=json_output)
        return 1
    except KeyboardInterrupt:
        _diagnostic(
            "interrupted",
            "Command interrupted; an in-flight mutation may have completed.",
            json_output=json_output,
        )
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
