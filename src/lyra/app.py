from fastapi import FastAPI
import uvicorn
import os

from contextlib import asynccontextmanager
from lyra.auth import initialize_earth_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_earth_engine()
    yield

    print("Shutting down worker...")


def main() -> None:
    initialize_earth_engine()

    # Defer imports until after authenticating with Earth Engine
    from lyra.routes import cvegeo, file, geojson

    app = FastAPI(title="Lyra API", version="0.1.0", lifespan=lifespan)
    app.include_router(cvegeo.router)
    app.include_router(file.router)
    app.include_router(geojson.router)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("LYRA_PORT", 8000)),
        reload=False,
    )
