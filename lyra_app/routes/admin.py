import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from lyra_app.plugins import format_update_message, reload_plugins
from lyra_app.registry import reload_tasks
from lyra_app.worker import graceful_worker_restart, register_tasks

router = APIRouter()

_bearer = HTTPBearer()


def require_admin_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    """FastAPI dependency that enforces admin API-key authentication.

    Reads the expected key from the `LYRA_ADMIN_API_KEY` environment variable.

    Args:
        credentials (HTTPAuthorizationCredentials): Bearer token extracted by
            FastAPI's `HTTPBearer` scheme.

    Raises:
        HTTPException: With status 500 if `LYRA_ADMIN_API_KEY` is not
            configured, or status 403 if the supplied token does not match.
    """
    expected = os.environ.get("LYRA_ADMIN_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="LYRA_ADMIN_API_KEY is not configured on the server.",
        )
    if credentials.credentials != expected:
        raise HTTPException(status_code=403, detail="Invalid admin API key.")


class UpdatePluginsResponse(BaseModel):
    updated_plugins: list[str]
    message: str


_TIMEOUT_QUERY = Query(
    ge=0.0,
    description=(
        "Seconds to wait for in-flight tasks to drain before forcing a worker restart."
    ),
)


@router.post("/update-plugins", dependencies=[Depends(require_admin_key)])
def update_plugins(
    timeout: Annotated[float, _TIMEOUT_QUERY] = 30.0,
) -> UpdatePluginsResponse:
    """Reclone changed plugin repos, hot-reload the task registry, and restart workers.

    Compares each repo's local HEAD against its remote HEAD, then reclones,
    verifies dependencies, and reinstalls only repos that have changed. Clears
    and re-discovers the in-process task registry, re-registers updated tasks
    with Celery, and gracefully drains workers before shutting them down so
    Docker restarts them with the new code.

    Args:
        timeout (float): Seconds to wait for in-flight tasks to drain before
            force-terminating them. Defaults to ``30.0``.

    Returns:
        UpdatePluginsResponse: Contains the list of updated plugin names and a
        human-readable summary message.
    """
    updated = reload_plugins()
    reload_tasks()
    register_tasks()
    graceful_worker_restart(timeout=timeout)

    return UpdatePluginsResponse(
        updated_plugins=updated,
        message=format_update_message(updated),
    )
