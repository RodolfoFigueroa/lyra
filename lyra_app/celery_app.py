from celery import Celery

from lyra_app.db.redis import redis_url

celery_app = Celery("ee_tasks", broker=redis_url, backend=redis_url)

__all__ = ["celery_app"]
