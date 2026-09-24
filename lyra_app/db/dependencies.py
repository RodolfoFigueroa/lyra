"""FastAPI dependency providers for database access."""

from typing import Annotated, cast

from fastapi import Depends, FastAPI, Request

from lyra_app.db.connection import ApplicationDatabaseRuntime


def require_database_runtime(app: FastAPI) -> ApplicationDatabaseRuntime:
    """Return the configured application database runtime.

    Raises:
        RuntimeError: If application construction did not supply a runtime.
    """
    database = getattr(app.state, "database", None)
    if database is None:
        msg = "Application database runtime is unavailable."
        raise RuntimeError(msg)
    return cast("ApplicationDatabaseRuntime", database)


def get_database_runtime(request: Request) -> ApplicationDatabaseRuntime:
    """Return the database runtime owned by the request's application."""
    return require_database_runtime(request.app)


DatabaseRuntimeDependency = Annotated[
    ApplicationDatabaseRuntime,
    Depends(get_database_runtime),
]
