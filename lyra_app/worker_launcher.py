"""Worker process command construction and launch orchestration."""

from __future__ import annotations

import argparse
import importlib
from typing import TYPE_CHECKING

from lyra_app.auth import initialize_earth_engine
from lyra_app.config import LyraConfig, ensure_runtime_directories, get_config
from lyra_app.db.connection import probe_worker_database
from lyra_app.db.redis import configure_redis
from lyra_app.logging_config import configure_logging
from lyra_app.plugin_state import PluginStateStore

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
    store: PluginStateStore | None = None,
) -> None:
    """Initialize dependencies, load plugins, and enter the Celery worker process.

    Raises:
        RuntimeError: If durable plugin state has not been initialized by the API.
    """
    config = get_config() if config is None else config
    config.get_worker(worker_name)
    ensure_runtime_directories(config)
    configure_logging(config)
    configure_redis(config)
    state_store = store or PluginStateStore(
        allowed_queues=config.plugins.allowed_queues,
    )
    if not state_store.path.is_file():
        msg = (
            "Plugin state is not initialized. Start the Lyra API and wait for "
            "it to become ready before starting workers."
        )
        raise RuntimeError(msg)

    probe_worker_database(config)
    initialize_earth_engine(config)

    celery_module = importlib.import_module("lyra_app.celery_app")
    worker_module = importlib.import_module("lyra_app.worker")
    celery_module.configure_celery(config)
    worker_module.refresh_runner_registry(
        worker_name,
        config=config,
        store=state_store,
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
