"""FastAPI dependency providers for database access."""

from typing import Annotated, cast

from fastapi import Depends, Request

from lyra_app.db.connection import ApplicationDatabaseRuntime


def get_database_runtime(request: Request) -> ApplicationDatabaseRuntime | None:
    """Read the optional application database runtime from FastAPI state.

    Returns:
        The managed runtime, or ``None`` when the application has none.
    """
    database = getattr(request.app.state, "database", None)
    if database is None:
        return None
    return cast("ApplicationDatabaseRuntime", database)


DatabaseRuntimeDependency = Annotated[
    ApplicationDatabaseRuntime | None,
    Depends(get_database_runtime),
]


__all__ = ["DatabaseRuntimeDependency", "get_database_runtime"]
