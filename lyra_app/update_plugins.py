import argparse
import logging

from lyra_app.logging_config import configure_logging
from lyra_app.plugins import format_update_message, reload_plugins

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI entry point: reload changed plugins, refresh the registry, restart workers.

    Parses ``--timeout``, reloads changed plugins, refreshes the task
    registry, restarts Celery workers, and prints a status message.
    """
    parser = argparse.ArgumentParser(
        description="Reclone changed plugin repos and hot-reload the Lyra workers.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help=(
            "Seconds to wait for in-flight tasks to drain before forcing "
            "a worker restart (default: 30)."
        ),
    )
    args = parser.parse_args()

    configure_logging()

    # reload_plugins() must run before importing lyra_app.registry or
    # lyra_app.worker. Those modules execute discover_tasks() / register_tasks()
    # at import time, which calls load_plugins() → _clone_or_update(), pulling
    # the latest commits before change detection can run. Importing them lazily
    # here, after reload_plugins() has set _PLUGINS_LOADED = True, prevents
    # load_plugins() from firing again on import.
    updated = reload_plugins()

    from lyra_app.registry import reload_tasks  # noqa: PLC0415
    from lyra_app.worker import graceful_worker_restart, register_tasks  # noqa: PLC0415

    reload_tasks()
    register_tasks()
    graceful_worker_restart(timeout=args.timeout)

    logger.info(format_update_message(updated))


if __name__ == "__main__":
    main()
