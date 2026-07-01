from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from lyra_app.config import LyraConfig, ensure_runtime_directories, get_config
from lyra_app.db.redis import configure_redis
from lyra_app.logging_config import configure_logging

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_celery_worker_args(config: LyraConfig, worker_name: str) -> list[str]:
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


def launch_worker(worker_name: str, *, config: LyraConfig | None = None) -> None:
    config = get_config() if config is None else config
    config.get_worker(worker_name)
    ensure_runtime_directories(config)
    configure_logging(config)
    configure_redis(config)

    from lyra_app.celery_app import celery_app, configure_celery  # noqa: PLC0415
    from lyra_app.worker import refresh_runner_registry  # noqa: PLC0415

    configure_celery(config)
    refresh_runner_registry(worker_name, config=config)
    celery_app.worker_main(build_celery_worker_args(config, worker_name))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Launch a Lyra Celery worker from /lyra_data/config/lyra.toml."
    )
    parser.add_argument("worker_name", help="Name from the [workers.<name>] table.")
    args = parser.parse_args(argv)
    try:
        launch_worker(args.worker_name)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        parser.error(message)


if __name__ == "__main__":
    main()
