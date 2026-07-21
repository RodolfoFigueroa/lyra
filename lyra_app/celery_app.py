"""Celery application configuration for Lyra background workers."""

from celery import Celery
from celery.signals import worker_process_shutdown

from lyra_app.config import LyraConfig
from lyra_app.db.connection import dispose_worker_engine
from lyra_app.db.redis import get_redis_url

celery_app = Celery("lyra")


@worker_process_shutdown.connect
def _dispose_worker_database(**_: object) -> None:
    dispose_worker_engine()


def configure_celery(config: LyraConfig | None = None) -> None:
    """Configure Celery broker and result storage from the shared Redis URL."""
    redis_url = get_redis_url(config)
    celery_app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
    )


__all__ = ["celery_app", "configure_celery"]
