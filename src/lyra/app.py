from fastapi import FastAPI
import uvicorn


from lyra.routes.base import router


app = FastAPI(title="Lyra API", version="0.1.0")
app.include_router(router)


def main() -> None:
    uvicorn.run("lyra.app:app", host="0.0.0.0", port=8000, reload=True)
