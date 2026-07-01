from typing import Any

import redis
import redis.asyncio as aioredis

from lyra_app.config import LyraConfig, get_config

_redis_client: Any | None = None
_redis_client_sync: Any | None = None


def get_redis_url(config: LyraConfig | None = None) -> str:
    config = get_config() if config is None else config
    return config.redis.url


def configure_redis(config: LyraConfig | None = None) -> str:
    global _redis_client, _redis_client_sync  # noqa: PLW0603

    redis_url = get_redis_url(config)
    _redis_client = aioredis.from_url(
        redis_url,
        socket_timeout=None,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
    _redis_client_sync = redis.from_url(redis_url)
    return redis_url


def _async_client() -> Any:
    if _redis_client is None:
        configure_redis()
    return _redis_client


def _sync_client() -> Any:
    if _redis_client_sync is None:
        configure_redis()
    return _redis_client_sync


class _RedisAsyncProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(_async_client(), name)


class _RedisSyncProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(_sync_client(), name)


redis_client = _RedisAsyncProxy()
redis_client_sync = _RedisSyncProxy()
