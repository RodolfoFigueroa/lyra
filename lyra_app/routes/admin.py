import hmac
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from lyra_app.config import ConfigLoadError, ConfigSecretError, LyraConfig, get_config
from lyra_app.plugin_state import (
    DEFAULT_PLUGIN_STATE_PATH,
    PluginRepoRecord,
    PluginState,
    PluginStateLoadError,
    PluginStateNotFoundError,
    PluginStateStore,
    PluginStateValidationError,
    repo_record_to_source,
)
from lyra_app.plugins import (
    PluginSyncError,
    format_update_message,
)
from lyra_app.plugins import (
    sync_plugin_repo as sync_plugin_source,
)
from lyra_app.registry import CatalogRefreshResult, refresh_catalog_from_state
from lyra_app.worker_control import graceful_worker_restart

_bearer = HTTPBearer()


def require_admin_key(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    """FastAPI dependency that enforces admin API-key authentication.

    Reads the expected key from the runtime configuration environment.

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


router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_key)])


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginRepoResponse(BaseModel):
    id: str
    source: str
    ref: str | None
    enabled: bool


class PluginRepoListResponse(BaseModel):
    repos: list[PluginRepoResponse]


class CreatePluginRepoRequest(StrictRequestModel):
    source: str
    id: str | None = None
    enabled: bool = True


class UpdatePluginRepoRequest(StrictRequestModel):
    source: str | None = None
    enabled: bool | None = None


class DeletePluginRepoResponse(BaseModel):
    deleted: bool
    repo_id: str


class SyncPluginRepoResponse(BaseModel):
    repo_id: str
    changed: bool
    display_name: str


class PluginCatalogRefreshResponse(BaseModel):
    updated_plugins: list[str]
    catalog_changed: bool
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str
    assigned_metric_queues: list[str]
    message: str


class PluginRoutingResponse(BaseModel):
    metric_queues: dict[str, str]
    allowed_queues: list[str]
    default_queue: str


class SetMetricQueueRequest(StrictRequestModel):
    queue: str


class MetricQueueAssignmentResponse(BaseModel):
    metric_name: str
    queue: str


class DeleteMetricQueueResponse(BaseModel):
    deleted: bool
    metric_name: str


_TIMEOUT_QUERY = Query(
    ge=0.0,
    description=(
        "Seconds to wait for in-flight tasks to drain before forcing a worker restart."
    ),
)


def get_plugin_state_path() -> Path:
    return DEFAULT_PLUGIN_STATE_PATH


def _load_config() -> LyraConfig:
    try:
        return get_config()
    except ConfigLoadError as exc:
        raise HTTPException(
            status_code=500,
            detail="Lyra config is not configured on the server.",
        ) from exc


def _state_store(config: LyraConfig | None = None) -> PluginStateStore:
    loaded_config = _load_config() if config is None else config
    return PluginStateStore(
        get_plugin_state_path(),
        allowed_queues=loaded_config.plugins.allowed_queues,
    )


def _repo_response(repo: PluginRepoRecord) -> PluginRepoResponse:
    return PluginRepoResponse(
        id=repo.id,
        source=repo.source,
        ref=repo.ref,
        enabled=repo.enabled,
    )


def _load_state(store: PluginStateStore) -> PluginState:
    try:
        return store.load()
    except PluginStateLoadError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Plugin state could not be loaded: {exc}",
        ) from exc


def _validation_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _not_found_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _sync_error_detail(exc: PluginSyncError | subprocess.CalledProcessError) -> str:
    if isinstance(exc, PluginSyncError):
        return str(exc)

    detail = exc.stderr or exc.stdout or str(exc)
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    return str(detail).strip() or str(exc)


def _catalog_refresh_response(
    result: CatalogRefreshResult,
) -> PluginCatalogRefreshResponse:
    return PluginCatalogRefreshResponse(
        updated_plugins=result.updated_plugins,
        catalog_changed=result.catalog_changed,
        previous_catalog_fingerprint=result.previous_catalog_fingerprint,
        catalog_fingerprint=result.catalog_fingerprint,
        assigned_metric_queues=result.assigned_metric_queues,
        message=format_update_message(
            result.updated_plugins,
            catalog_changed=result.catalog_changed,
            catalog_fingerprint=result.catalog_fingerprint,
        ),
    )


@router.get("/plugin-repos")
def list_plugin_repos() -> PluginRepoListResponse:
    store = _state_store()
    state = _load_state(store)
    return PluginRepoListResponse(repos=[_repo_response(repo) for repo in state.repos])


@router.post("/plugin-repos")
def create_plugin_repo(request: CreatePluginRepoRequest) -> PluginRepoResponse:
    store = _state_store()
    try:
        repo = store.add_repo(
            request.source,
            repo_id=request.id,
            enabled=request.enabled,
        )
    except (PluginStateValidationError, ValueError) as exc:
        raise _validation_error(exc) from exc
    return _repo_response(repo)


@router.patch("/plugin-repos/{repo_id}")
def update_plugin_repo(
    repo_id: str,
    request: UpdatePluginRepoRequest,
) -> PluginRepoResponse:
    store = _state_store()
    try:
        repo = store.update_repo(
            repo_id,
            source=request.source,
            enabled=request.enabled,
        )
    except PluginStateNotFoundError as exc:
        raise _not_found_error(exc) from exc
    except (PluginStateValidationError, ValueError) as exc:
        raise _validation_error(exc) from exc
    return _repo_response(repo)


@router.delete("/plugin-repos/{repo_id}")
def delete_plugin_repo(repo_id: str) -> DeletePluginRepoResponse:
    store = _state_store()
    try:
        deleted = store.delete_repo(repo_id)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"unknown plugin repo id: {repo_id}",
        )
    return DeletePluginRepoResponse(deleted=True, repo_id=repo_id)


@router.post("/plugin-repos/{repo_id}/sync")
def sync_plugin_repo(repo_id: str) -> SyncPluginRepoResponse:
    config = _load_config()
    store = _state_store(config)
    state = _load_state(store)
    repo = next(
        (candidate for candidate in state.repos if candidate.id == repo_id), None
    )
    if repo is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown plugin repo id: {repo_id}",
        )
    if not repo.enabled:
        raise HTTPException(
            status_code=409,
            detail=f"plugin repo is disabled: {repo_id}",
        )

    try:
        synced = sync_plugin_source(
            config.plugins.catalog_dir, repo_record_to_source(repo)
        )
    except (PluginSyncError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=502, detail=_sync_error_detail(exc)) from exc

    return SyncPluginRepoResponse(
        repo_id=repo.id,
        changed=synced.changed,
        display_name=synced.entry.display_name,
    )


@router.post("/plugin-catalog/refresh")
def refresh_plugin_catalog(
    timeout: Annotated[float, _TIMEOUT_QUERY] = 30.0,
) -> PluginCatalogRefreshResponse:
    config = _load_config()
    store = _state_store(config)
    try:
        result = refresh_catalog_from_state(store)
    except (PluginSyncError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=502, detail=_sync_error_detail(exc)) from exc
    except (PluginStateLoadError, PluginStateValidationError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Plugin state could not be used: {exc}",
        ) from exc
    graceful_worker_restart(timeout=timeout)
    return _catalog_refresh_response(result)


@router.get("/plugin-routing")
def list_plugin_routing() -> PluginRoutingResponse:
    config = _load_config()
    store = _state_store(config)
    state = _load_state(store)
    return PluginRoutingResponse(
        metric_queues=state.metric_queues,
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
    )


@router.put("/plugin-routing/{metric_name}")
def set_plugin_routing(
    metric_name: str,
    request: SetMetricQueueRequest,
) -> MetricQueueAssignmentResponse:
    store = _state_store()
    try:
        queue = store.set_metric_queue(metric_name, request.queue)
    except (PluginStateValidationError, ValueError) as exc:
        raise _validation_error(exc) from exc
    return MetricQueueAssignmentResponse(metric_name=metric_name.strip(), queue=queue)


@router.delete("/plugin-routing/{metric_name}")
def delete_plugin_routing(metric_name: str) -> DeleteMetricQueueResponse:
    store = _state_store()
    try:
        deleted = store.delete_metric_queue(metric_name)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    return DeleteMetricQueueResponse(deleted=deleted, metric_name=metric_name.strip())
