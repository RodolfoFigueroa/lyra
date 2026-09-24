"""FastAPI application factory and service lifecycle management."""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from importlib import import_module
from types import AsyncGeneratorType

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lyra_app import registry
from lyra_app.auth import initialize_earth_engine
from lyra_app.celery_app import configure_celery
from lyra_app.config import (
    LyraConfig,
    ensure_runtime_directories,
    get_config,
    initialize_runtime_config,
)
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.db.dependencies import require_database_runtime
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
    """Manage database, worker-inspection, and optional MCP application lifecycles.

    Yields:
        Control to FastAPI while all configured application resources are active.
    """
    database = require_database_runtime(app)
    try:
        await database.start()
        start_worker_inspect_collector()
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
    finally:
        await database.close()


def bootstrap_runtime(config: LyraConfig | None = None) -> LyraConfig:
    """Initialize process-wide directories, clients, logging, and Earth Engine.

    Returns:
        The validated configuration used to initialize the process.
    """
    config = get_config() if config is None else config
    initialize_runtime_config(config)
    ensure_runtime_directories(config)
    configure_logging(config)
    configure_redis(config)
    configure_celery(config)
    initialize_earth_engine(config)
    return config


def create_app(config: LyraConfig | None = None) -> FastAPI:
    """Create and configure the Lyra HTTP and optional MCP application.

    Returns:
        A fully routed FastAPI application with managed database state.
    """
    config = bootstrap_runtime(config)
    registry.initialize_catalog(config)

    # Defer imports until after authenticating with Earth Engine
    admin = import_module("lyra_app.routes.admin")
    data_types = import_module("lyra_app.routes.data_types")
    health = import_module("lyra_app.routes.health")
    jobs = import_module("lyra_app.routes.jobs")
    met_zone = import_module("lyra_app.routes.met_zone")
    metrics = import_module("lyra_app.routes.metrics")

    app = FastAPI(title="Lyra API", version=APP_VERSION, lifespan=lifespan)
    app.add_exception_handler(
        registry.CatalogUnavailableError, catalog_unavailable_response
    )
    app.state.database = ApplicationDatabaseRuntime(config)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)
    app.include_router(met_zone.router)
    if config.mcp.enabled:
        mcp_module = import_module("lyra_app.mcp")
        mcp_tools = import_module("lyra_app.mcp.tools")
        mcp_app = mcp_module.create_mcp_app(
            agent_api_key=config.agent.read_api_key(),
            public_api_base_url=config.api.public_base_url,
            backend=mcp_tools.InProcessLyraBackend(app.state.database),
        )
        app.state.mcp_app = mcp_app
        app.mount(config.mcp.mount_path, mcp_app)
    return app


def catalog_unavailable_response(_request: Request, _exc: Exception) -> JSONResponse:
    """Report a retryable catalog startup failure without exposing source details.

    Returns:
        A service-unavailable response.
    """
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "catalog_unavailable",
                "message": (
                    "Plugin catalog is unavailable; inspect administrative status."
                ),
                "retryable": True,
            }
        },
    )


def run_server(config: LyraConfig | None = None) -> None:
    """Create and run the production Uvicorn server from runtime configuration."""
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
