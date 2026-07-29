"""HTTP endpoint for resolving meteorological zones."""

import logging

from fastapi import APIRouter, HTTPException
from lyra.sdk.db import LyraDatabaseError
from lyra.sdk.models import MetZoneCodeResponse
from lyra.sdk.postgres import classify_postgres_error
from sqlalchemy.exc import SQLAlchemyError

from lyra_app.db.dependencies import DatabaseRuntimeDependency
from lyra_app.loaders.db import get_met_zone_code_from_name_async
from lyra_app.routes.errors import database_error_http_exception

router = APIRouter(tags=["Lookups"])
logger = logging.getLogger(__name__)


@router.get("/lookups/met-zones")
async def get_met_zone_code(
    name: str,
    database: DatabaseRuntimeDependency,
) -> MetZoneCodeResponse:
    """Resolve a metropolitan-zone name through the application database.

    Returns:
        The canonical metropolitan-zone code and official name.

    Raises:
        RuntimeError: If the application database runtime is unavailable.
        HTTPException: If the database operation fails or no zone matches the name.
    """
    if database is None:
        msg = "Application database runtime is unavailable."
        raise RuntimeError(msg)
    try:
        async with database.require_async_engine().connect() as connection:
            result = await get_met_zone_code_from_name_async(
                name,
                conn=connection,
                schema=database.config.database.data_schema,
            )
    except SQLAlchemyError as exc:
        classified = classify_postgres_error(exc)
        if not classified.retryable:
            logger.exception("Database query failed while resolving a met zone.")
        error = database_error_http_exception(classified, database.config)
        raise error from exc
    except LyraDatabaseError as exc:
        if not exc.retryable:
            logger.exception("Database query failed while resolving a met zone.")
        error = database_error_http_exception(exc, database.config)
        raise error from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No metropolitan zone matched the given name.",
        )

    cve_met, nom_met = result
    return MetZoneCodeResponse(cve_met=cve_met, nom_met=nom_met)
