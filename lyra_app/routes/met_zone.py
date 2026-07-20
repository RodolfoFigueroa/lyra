from fastapi import APIRouter, HTTPException
from lyra.sdk.models import MetZoneCodeResponse
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.db.connection import (
    is_database_unavailable_error,
)
from lyra_app.db.dependencies import DatabaseRuntimeDependency
from lyra_app.loaders.db import get_met_zone_code_from_name_async
from lyra_app.routes.errors import database_unavailable_http_exception

router = APIRouter(tags=["Lookups"])


@router.get("/lookups/met-zones")
async def get_met_zone_code(
    name: str,
    database: DatabaseRuntimeDependency,
) -> MetZoneCodeResponse:
    if database is None:
        msg = "Application database runtime is unavailable."
        raise RuntimeError(msg)
    try:
        async with database.require_async_engine().connect() as connection:
            result = await get_met_zone_code_from_name_async(name, conn=connection)
    except SQLAlchemyError as exc:
        if not is_database_unavailable_error(exc):
            raise
        raise database_unavailable_http_exception(database.config) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No metropolitan zone matched the given name.",
        )

    cve_met, nom_met = result
    return MetZoneCodeResponse(cve_met=cve_met, nom_met=nom_met)
