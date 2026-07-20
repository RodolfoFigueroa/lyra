from celery import Celery
from celery.signals import worker_process_shutdown

from lyra_app.config import LyraConfig
from lyra_app.db.redis import get_redis_url

celery_app = Celery("lyra")


@worker_process_shutdown.connect
def _dispose_worker_database(**_: object) -> None:
    from lyra_app.db.connection import dispose_worker_engine  # noqa: PLC0415

    dispose_worker_engine()


def configure_celery(config: LyraConfig | None = None) -> None:
    redis_url = get_redis_url(config)
    celery_app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
    )


__all__ = ["celery_app", "configure_celery"]
