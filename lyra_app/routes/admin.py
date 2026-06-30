import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from lyra_app.config import ConfigLoadError, ConfigSecretError, get_config
from lyra_app.plugins import format_update_message
from lyra_app.registry import refresh_catalog
from lyra_app.worker_control import graceful_worker_restart

router = APIRouter()

_bearer = HTTPBearer()


def require_admin_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    """FastAPI dependency that enforces admin API-key authentication.

    Reads the expected key from the TOML-configured secret file.

    Args:
        credentials (HTTPAuthorizationCredentials): Bearer token extracted by
            FastAPI's `HTTPBearer` scheme.

    Raises:
        HTTPException: With status 500 if the configured secret cannot be
            loaded, or status 403 if the supplied token does not match.
    """
    try:
        expected = get_config().admin.read_api_key()
    except (ConfigLoadError, ConfigSecretError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Admin API key is not configured on the server.",
        ) from exc
    if not hmac.compare_digest(credentials.credentials, expected):
        raise HTTPException(status_code=403, detail="Invalid admin API key.")


class UpdatePluginsResponse(BaseModel):
    updated_plugins: list[str]
    catalog_changed: bool
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str
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
    """Reclone plugin catalog repos, refresh the manifest registry, and restart workers.

    Compares each catalog repo's local HEAD against its remote HEAD, reloads
    static manifests into the API registry, and gracefully drains workers before
    shutting them down so Docker restarts them with the new plugin code.

    Args:
        timeout (float): Seconds to wait for in-flight tasks to drain before
            force-terminating them. Defaults to ``30.0``.

    Returns:
        UpdatePluginsResponse: Contains the list of updated plugin names and a
        human-readable summary message.
    """
    result = refresh_catalog()
    graceful_worker_restart(timeout=timeout)

    return UpdatePluginsResponse(
        updated_plugins=result.updated_plugins,
        catalog_changed=result.catalog_changed,
        previous_catalog_fingerprint=result.previous_catalog_fingerprint,
        catalog_fingerprint=result.catalog_fingerprint,
        message=format_update_message(
            result.updated_plugins,
            catalog_changed=result.catalog_changed,
            catalog_fingerprint=result.catalog_fingerprint,
        ),
    )
