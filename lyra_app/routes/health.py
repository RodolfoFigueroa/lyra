import asyncio

from fastapi import APIRouter, Response, status
from lyra.sdk.models import (
    DatabaseHealth,
    LivenessResponse,
    ReadinessResponse,
    RedisHealth,
)
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.db.dependencies import DatabaseRuntimeDependency
from lyra_app.db.redis import redis_client
from lyra_app.version import APP_VERSION

router = APIRouter(tags=["Health"])


async def redis_health(timeout_seconds: float) -> RedisHealth:
    try:
        async with asyncio.timeout(timeout_seconds):
            pong = await redis_client.ping()
    except (TimeoutError, RedisError):
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


async def database_health(
    database: ApplicationDatabaseRuntime,
    timeout_seconds: float,
) -> DatabaseHealth:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with database.require_async_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError):
        return DatabaseHealth(status="unavailable")
    return DatabaseHealth(status="ok")


@router.get("/live")
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok", api_version=APP_VERSION)


@router.get("/ready")
async def readiness(
    response: Response,
    database: DatabaseRuntimeDependency,
) -> ReadinessResponse:
    if database is None:
        msg = "Application database runtime is unavailable."
        raise RuntimeError(msg)
    timeout_seconds = database.config.database.readiness_timeout_seconds
    redis, postgres = await asyncio.gather(
        redis_health(timeout_seconds),
        database_health(database, timeout_seconds),
    )
    is_ready = redis.status == "ok" and postgres.status == "ok"
    response.status_code = (
        status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    response.headers["Cache-Control"] = "no-store"
    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        api_version=APP_VERSION,
        redis=redis,
        database=postgres,
    )


__all__ = ["database_health", "liveness", "readiness", "redis_health", "router"]
