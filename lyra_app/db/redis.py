"""Redis-backed coordination and caching helpers."""

from collections.abc import Awaitable, Callable
from typing import TypeAlias

import redis
import redis.asyncio as aioredis

from lyra_app.config import LyraConfig, get_config

RedisValue: TypeAlias = (
    bool
    | int
    | float
    | str
    | bytes
    | list["RedisValue"]
    | dict[str | bytes, "RedisValue"]
    | None
)
AsyncRedisMethod: TypeAlias = Callable[..., Awaitable[RedisValue]]
SyncRedisMethod: TypeAlias = Callable[..., RedisValue]

_redis_client: aioredis.Redis | None = None
_redis_client_sync: redis.Redis | None = None


def get_redis_url(config: LyraConfig | None = None) -> str:
    """Return the validated Redis URL from explicit or process configuration.

    Returns:
        The Redis connection URL shared by coordination clients.
    """
    config = get_config() if config is None else config
    return config.redis.url


def configure_redis(config: LyraConfig | None = None) -> str:
    """Replace process-wide synchronous and asynchronous Redis clients.

    Returns:
        The Redis URL used to create both clients.
    """
    global _redis_client, _redis_client_sync  # ruff:ignore[global-statement]

    redis_url = get_redis_url(config)
    _redis_client = aioredis.from_url(
        redis_url,
        socket_timeout=None,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
    _redis_client_sync = redis.from_url(redis_url)
    return redis_url


def _async_client() -> aioredis.Redis:
    if _redis_client is None:
        configure_redis()
    if _redis_client is None:  # pragma: no cover - configure_redis always assigns
        msg = "Redis async client initialization failed"
        raise RuntimeError(msg)
    return _redis_client


def _sync_client() -> redis.Redis:
    if _redis_client_sync is None:
        configure_redis()
    if _redis_client_sync is None:  # pragma: no cover - configure_redis always assigns
        msg = "Redis sync client initialization failed"
        raise RuntimeError(msg)
    return _redis_client_sync


class _RedisAsyncProxy:
    def __getattr__(self, name: str) -> AsyncRedisMethod:
        return getattr(_async_client(), name)


class _RedisSyncProxy:
    def __getattr__(self, name: str) -> SyncRedisMethod:
        return getattr(_sync_client(), name)


redis_client = _RedisAsyncProxy()
redis_client_sync = _RedisSyncProxy()
