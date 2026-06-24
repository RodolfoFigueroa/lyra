import logging
import os
from contextlib import asynccontextmanager
from types import AsyncGeneratorType

import uvicorn
from fastapi import FastAPI

from lyra_app.auth import initialize_earth_engine
from lyra_app.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGeneratorType:  # noqa: ARG001
    configure_logging()
    initialize_earth_engine()
    yield

    logger.info("Shutting down worker.")


if __name__ == "__main__":
    configure_logging()
    initialize_earth_engine()

    from lyra_app.registry import ensure_catalog_loaded

    ensure_catalog_loaded()

    # Defer imports until after authenticating with Earth Engine
    from lyra_app.routes import (
        admin,
        data_types,
        jobs,
        met_zone,
        metrics,
    )

    app = FastAPI(title="Lyra API", version="0.1.0", lifespan=lifespan)
    app.include_router(admin.router)
    app.include_router(jobs.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)
    app.include_router(met_zone.router)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("LYRA_PORT", "5219")),
        reload=False,
    )
