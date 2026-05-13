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

    # Defer imports until after authenticating with Earth Engine
    from lyra_app.routes import (
        data_types,
        download,
        geojson,
        met_zone,
        metrics,
        models,
    )

    app = FastAPI(title="Lyra API", version="0.1.0", lifespan=lifespan)
    app.include_router(geojson.router)
    app.include_router(download.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)
    app.include_router(met_zone.router)
    app.include_router(models.router)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("LYRA_PORT", "5219")),
        reload=False,
        ws_max_size=5_000_000,
    )
