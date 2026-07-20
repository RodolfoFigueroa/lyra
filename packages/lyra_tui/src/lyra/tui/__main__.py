from __future__ import annotations

import argparse
import os
from math import isfinite
from typing import TYPE_CHECKING

from lyra.tui.app import LyraTuiApp
from lyra.tui.config import TuiConfig

if TYPE_CHECKING:
    from collections.abc import Sequence


class _HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        err = "must be a number"
        raise argparse.ArgumentTypeError(err) from exc
    if parsed <= 0 or not isfinite(parsed):
        err = "must be a finite number greater than 0"
        raise argparse.ArgumentTypeError(err)
    return parsed


def _api_host(value: str) -> str:
    host = value.strip().rstrip("/")
    if not host:
        err = "must not be empty"
        raise argparse.ArgumentTypeError(err)
    if "://" in host:
        err = "omit the URL scheme; use --secure or --no-secure instead"
        raise argparse.ArgumentTypeError(err)
    return host


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyra-tui",
        formatter_class=_HelpFormatter,
        description=(
            "Run the Lyra terminal operator console.\n\n"
            "The TUI connects to an already-running Lyra API. Without an admin "
            "key it can only show public readiness information; admin views and "
            "mutating actions require Bearer auth."
        ),
        epilog=(
            "Examples:\n"
            "  uv run lyra-tui --host localhost:5219 --no-secure\n"
            "  LYRA_ADMIN_API_KEY=... uv run lyra-tui --host localhost:5219 "
            "--no-secure"
        ),
    )
    parser.add_argument(
        "--host",
        default="localhost:5219",
        type=_api_host,
        metavar="HOST[:PORT]",
        help=(
            "Lyra API host and optional port, without http:// or https://. "
            "Default: localhost:5219."
        ),
    )
    parser.add_argument(
        "--admin-api-key",
        default=None,
        metavar="TOKEN",
        help="Admin API bearer token. Falls back to LYRA_ADMIN_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=30.0,
        metavar="SECONDS",
        help="HTTP request timeout in seconds. Default: 30.",
    )
    parser.add_argument(
        "--refresh-interval",
        type=_positive_float,
        default=5.0,
        metavar="SECONDS",
        help="Status refresh interval in seconds. Default: 5.",
    )
    secure_group = parser.add_mutually_exclusive_group()
    secure_group.add_argument(
        "--secure",
        dest="secure",
        action="store_true",
        help="Use HTTPS when connecting to Lyra.",
    )
    secure_group.add_argument(
        "--no-secure",
        dest="secure",
        action="store_false",
        help="Use HTTP when connecting to Lyra.",
    )
    parser.set_defaults(secure=False)
    return parser


def config_from_args(args: argparse.Namespace) -> TuiConfig:
    return TuiConfig(
        host=args.host,
        secure=args.secure,
        admin_api_key=args.admin_api_key or os.getenv("LYRA_ADMIN_API_KEY"),
        timeout=args.timeout,
        refresh_interval=args.refresh_interval,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app = LyraTuiApp(config_from_args(args))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
