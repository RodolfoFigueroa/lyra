from celery import Celery

from lyra_app.db.redis import redis_url

celery_app = Celery("lyra", broker=redis_url, backend=redis_url)

__all__ = ["celery_app"]
