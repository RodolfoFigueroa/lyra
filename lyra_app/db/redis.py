import os

import redis
import redis.asyncio as aioredis

redis_url = os.getenv("CELERY_BROKER_URL", "redis://lyra-redis:6379/0")

redis_client = aioredis.from_url(
    redis_url,
    socket_timeout=None,
    socket_connect_timeout=5,
    health_check_interval=30,
)

redis_client_sync = redis.from_url(redis_url)
