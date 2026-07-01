from celery import Celery

from lyra_app.config import LyraConfig
from lyra_app.db.redis import get_redis_url

celery_app = Celery("lyra")


def configure_celery(config: LyraConfig | None = None) -> None:
    redis_url = get_redis_url(config)
    celery_app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
    )


__all__ = ["celery_app", "configure_celery"]
