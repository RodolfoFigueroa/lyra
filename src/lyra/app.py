from fastapi import FastAPI
import uvicorn

from contextlib import asynccontextmanager
from lyra.routes.base import router
from lyra.auth import initialize_earth_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_earth_engine()
    yield

    print("Shutting down worker...")


app = FastAPI(title="Lyra API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


def main() -> None:
    uvicorn.run("lyra.app:app", host="0.0.0.0", port=8000, reload=True)
