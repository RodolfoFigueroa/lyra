from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

from lyra.tui.app import LyraTuiApp
from lyra.tui.config import TuiConfig

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lyra-tui",
        description="Run the Lyra terminal operator console.",
    )
    parser.add_argument(
        "--host",
        default="localhost:5219",
        help="Lyra API host and optional port.",
    )
    parser.add_argument(
        "--admin-api-key",
        default=None,
        help="Admin API bearer token. Defaults to LYRA_ADMIN_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds.",
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
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app = LyraTuiApp(config_from_args(args))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
