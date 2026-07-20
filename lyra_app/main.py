import logging
from contextlib import AsyncExitStack, asynccontextmanager
from types import AsyncGeneratorType

import uvicorn
from fastapi import FastAPI

from lyra_app.auth import initialize_earth_engine
from lyra_app.celery_app import configure_celery
from lyra_app.config import LyraConfig, ensure_runtime_directories, get_config
from lyra_app.db.redis import configure_redis
from lyra_app.logging_config import configure_logging
from lyra_app.version import APP_VERSION
from lyra_app.worker_control import (
    start_worker_inspect_collector,
    stop_worker_inspect_collector,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGeneratorType:
    await start_worker_inspect_collector()
    try:
        async with AsyncExitStack() as stack:
            mcp_app = getattr(app.state, "mcp_app", None)
            if mcp_app is not None:
                await stack.enter_async_context(
                    mcp_app.router.lifespan_context(mcp_app)
                )
            yield
    finally:
        await stop_worker_inspect_collector()
        logger.info("Shutting down worker inspect collector.")


def bootstrap_runtime(config: LyraConfig | None = None) -> LyraConfig:
    config = get_config() if config is None else config
    ensure_runtime_directories(config)
    configure_logging(config)
    configure_redis(config)
    configure_celery(config)
    initialize_earth_engine(config)
    return config


def create_app(config: LyraConfig | None = None) -> FastAPI:
    config = bootstrap_runtime(config)
    from lyra_app.registry import ensure_catalog_loaded  # noqa: PLC0415

    ensure_catalog_loaded()

    # Defer imports until after authenticating with Earth Engine
    from lyra_app.routes import (  # noqa: PLC0415
        admin,
        data_types,
        health,
        jobs,
        met_zone,
        metrics,
    )

    app = FastAPI(title="Lyra API", version=APP_VERSION, lifespan=lifespan)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)
    app.include_router(met_zone.router)
    if config.mcp.enabled:
        from lyra_app.mcp import create_mcp_app  # noqa: PLC0415

        mcp_app = create_mcp_app(
            agent_api_key=config.agent.read_api_key(),
            public_api_base_url=config.api.public_base_url,
        )
        app.state.mcp_app = mcp_app
        app.mount(config.mcp.mount_path, mcp_app)
    return app


def run_server(config: LyraConfig | None = None) -> None:
    runtime_config = get_config() if config is None else config
    app = create_app(runtime_config)

    uvicorn.run(
        app,
        host=runtime_config.api.host,
        port=runtime_config.api.port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips=runtime_config.api.forwarded_allow_ips,
    )


if __name__ == "__main__":
    run_server()
