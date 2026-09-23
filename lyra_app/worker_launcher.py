"""Worker process command construction and launch orchestration."""

from __future__ import annotations

import argparse
import importlib
from typing import TYPE_CHECKING

from lyra_app.auth import initialize_earth_engine
from lyra_app.config import (
    LyraConfig,
    ensure_runtime_directories,
    get_config,
    initialize_runtime_config,
)
from lyra_app.db.connection import probe_worker_database
from lyra_app.db.redis import configure_redis
from lyra_app.logging_config import configure_logging

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_celery_worker_args(config: LyraConfig, worker_name: str) -> list[str]:
    """Build Celery worker arguments from one named worker configuration.

    Returns:
        Arguments selecting hostname, logging, concurrency, and consumed queues.
    """
    worker = config.get_worker(worker_name)
    return [
        "worker",
        "--hostname",
        f"{worker_name}@%h",
        "--loglevel",
        config.logging.level,
        "--concurrency",
        str(worker.concurrency),
        "-Q",
        ",".join(worker.queues),
    ]


def launch_worker(
    worker_name: str,
    *,
    config: LyraConfig | None = None,
) -> None:
    """Initialize dependencies, load plugins, and enter the Celery worker process."""
    config = get_config() if config is None else config
    config.get_worker(worker_name)
    initialize_runtime_config(config)
    ensure_runtime_directories(config)
    configure_logging(config)
    configure_redis(config)
    probe_worker_database(config)
    initialize_earth_engine(config)

    celery_module = importlib.import_module("lyra_app.celery_app")
    worker_module = importlib.import_module("lyra_app.worker")
    celery_module.configure_celery(config)
    worker_module.refresh_runner_registry(
        worker_name,
        config=config,
    )
    celery_module.celery_app.worker_main(build_celery_worker_args(config, worker_name))


def build_parser() -> argparse.ArgumentParser:
    """Build the supported worker-launcher argument parser.

    Returns:
        The parser requiring a configured worker name.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lyra_app.worker_launcher",
        description="Launch a Lyra Celery worker from /lyra_data/config/lyra.toml.",
    )
    parser.add_argument("worker_name", help="Name from the [workers.<name>] table.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Launch a configured worker from command-line arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        launch_worker(args.worker_name)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        parser.error(message)


if __name__ == "__main__":
    main()
