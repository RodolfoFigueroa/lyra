import argparse
import logging

from lyra_app.celery_app import configure_celery
from lyra_app.config import get_config
from lyra_app.db.redis import configure_redis
from lyra_app.logging_config import configure_logging
from lyra_app.plugins import format_update_message
from lyra_app.registry import refresh_catalog
from lyra_app.worker_control import graceful_worker_restart

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI entry point: refresh plugin manifests and restart workers.

    Parses ``--timeout``, refreshes the API manifest catalog, restarts Celery
    workers, and prints a status message.
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

    config = get_config()
    configure_logging(config)
    configure_redis(config)
    configure_celery(config)

    result = refresh_catalog()
    graceful_worker_restart(timeout=args.timeout)

    logger.info(
        format_update_message(
            result.updated_plugins,
            catalog_changed=result.catalog_changed,
            catalog_fingerprint=result.catalog_fingerprint,
        )
    )


if __name__ == "__main__":
    main()
