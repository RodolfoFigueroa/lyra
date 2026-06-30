import redis
import redis.asyncio as aioredis

from lyra_app.config import ConfigLoadError, get_config

_FALLBACK_REDIS_URL = "redis://lyra-redis:6379/0"


def get_redis_url() -> str:
    try:
        return get_config().redis.url
    except ConfigLoadError:
        return _FALLBACK_REDIS_URL


redis_url = get_redis_url()

redis_client = aioredis.from_url(
    redis_url,
    socket_timeout=None,
    socket_connect_timeout=5,
    health_check_interval=30,
)

redis_client_sync = redis.from_url(redis_url)
