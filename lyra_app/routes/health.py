from fastapi import APIRouter
from lyra.sdk.models import HealthResponse, RedisHealth
from redis.exceptions import RedisError

from lyra_app.db.redis import redis_client
from lyra_app.version import APP_VERSION

router = APIRouter()


async def _redis_health() -> RedisHealth:
    try:
        pong = await redis_client.ping()
    except RedisError:
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


@router.get("/health")
async def health_check() -> HealthResponse:
    redis = await _redis_health()
    return HealthResponse(
        status="ok" if redis.status == "ok" else "degraded",
        api_version=APP_VERSION,
        redis=redis,
    )
