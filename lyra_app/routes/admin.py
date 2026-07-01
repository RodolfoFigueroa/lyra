import hmac
import subprocess
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    CreatePluginRepoRequest,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    JobCancelResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    MetricQueueAssignmentResponse,
    PluginCatalogRefreshResponse,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    PluginSourceSummary,
    QueuesResponse,
    QueueSummary,
    RedisHealth,
    SetMetricQueueRequest,
    SyncPluginRepoResponse,
    UpdatePluginRepoRequest,
    WorkerConfigSummary,
    WorkerDetail,
    WorkerRestartResponse,
    WorkersResponse,
    WorkerSummary,
    WorkerTaskSummary,
)
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.config import ConfigLoadError, ConfigSecretError, LyraConfig, get_config
from lyra_app.plugin_state import (
    DEFAULT_PLUGIN_STATE_PATH,
    PluginRepoRecord,
    PluginState,
    PluginStateLoadError,
    PluginStateNotFoundError,
    PluginStateStore,
    PluginStateValidationError,
    normalize_repo_source,
    repo_record_to_source,
)
from lyra_app.plugins import (
    PluginSyncError,
    format_update_message,
)
from lyra_app.plugins import (
    sync_plugin_repo as sync_plugin_source,
)
from lyra_app.registry import (
    CatalogRefreshResult,
    get_loaded_catalog_fingerprint,
    get_loaded_metric_names,
    get_loaded_metric_queues,
    refresh_catalog_from_state,
)
from lyra_app.version import APP_VERSION
from lyra_app.worker_control import (
    WorkerInspectSnapshot,
    get_worker_inspect_snapshot,
    graceful_worker_restart,
    revoke_job,
    safe_task_summary,
)

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


_TIMEOUT_QUERY = Query(
    ge=0.0,
    description=(
        "Seconds to wait for in-flight tasks to drain before forcing a worker restart."
    ),
)

_JOB_LIMIT_QUERY = Query(
    ge=1,
    le=100,
    description="Maximum number of recent jobs to return.",
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


def _redis_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Cannot connect to Redis. Please try again later.",
    )


def _redis_health() -> RedisHealth:
    try:
        pong = job_store.redis_client_sync.ping()
    except RedisError:
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


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
    restart_recommended = bool(
        result.updated_plugins
        or result.catalog_changed
        or result.assigned_metric_queues
    )
    return PluginCatalogRefreshResponse(
        updated_plugins=result.updated_plugins,
        catalog_changed=result.catalog_changed,
        previous_catalog_fingerprint=result.previous_catalog_fingerprint,
        catalog_fingerprint=result.catalog_fingerprint,
        assigned_metric_queues=result.assigned_metric_queues,
        workers_restarted=False,
        workers_restart_recommended=restart_recommended,
        message=format_update_message(
            result.updated_plugins,
            catalog_changed=result.catalog_changed,
            catalog_fingerprint=result.catalog_fingerprint,
            workers_restarting=False,
        ),
    )


def _worker_config_summary(config: LyraConfig, worker_name: str) -> WorkerConfigSummary:
    worker = config.get_worker(worker_name)
    return WorkerConfigSummary(
        name=worker_name,
        queues=worker.queues,
        concurrency=worker.concurrency,
        install_dir=str(config.worker_install_dir(worker_name)),
        temp_dir=str(config.worker_temp_dir(worker_name)),
    )


def _plugin_source_summary(repo: PluginRepoRecord) -> PluginSourceSummary:
    normalized = normalize_repo_source(repo.source)
    return PluginSourceSummary(
        id=repo.id,
        source=repo.source,
        source_kind=normalized.source_kind,
        ref=repo.ref,
        enabled=repo.enabled,
    )


def _task_summaries(
    section: dict[str, list[dict[str, Any]]] | None,
    worker_name: str,
) -> list[WorkerTaskSummary]:
    if section is None:
        return []
    return [
        WorkerTaskSummary.model_validate(
            safe_task_summary(task, worker_name=worker_name)
        )
        for task in section.get(worker_name, [])
    ]


def _task_count(
    section: dict[str, list[dict[str, Any]]] | None,
    worker_name: str,
) -> int | None:
    if section is None:
        return None
    return len(section.get(worker_name, []))


def _worker_queues(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> list[str]:
    queues: set[str] = set()
    if worker_name in config.workers:
        queues.update(config.get_worker(worker_name).queues)
    if snapshot.active_queues is not None:
        queues.update(snapshot.active_queues.get(worker_name, []))
    return sorted(queues)


def _worker_summary(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> WorkerSummary:
    configured = worker_name in config.workers
    observed = worker_name in snapshot.observed_worker_names
    status = (
        "unknown"
        if not snapshot.inspect_available
        else "online"
        if observed
        else "offline"
    )
    return WorkerSummary(
        name=worker_name,
        configured=configured,
        observed=observed,
        status=status,
        queues=_worker_queues(config, snapshot, worker_name),
        active_count=_task_count(snapshot.active, worker_name),
        reserved_count=_task_count(snapshot.reserved, worker_name),
        scheduled_count=_task_count(snapshot.scheduled, worker_name),
    )


def _worker_detail(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> WorkerDetail:
    summary = _worker_summary(config, snapshot, worker_name)
    return WorkerDetail(
        **summary.model_dump(mode="json"),
        active_tasks=_task_summaries(snapshot.active, worker_name),
        reserved_tasks=_task_summaries(snapshot.reserved, worker_name),
        scheduled_tasks=_task_summaries(snapshot.scheduled, worker_name),
        stats=(snapshot.stats or {}).get(worker_name),
    )


def _all_worker_names(config: LyraConfig, snapshot: WorkerInspectSnapshot) -> list[str]:
    return sorted(set(config.workers) | snapshot.observed_worker_names)


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
def refresh_plugin_catalog() -> PluginCatalogRefreshResponse:
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
    return _catalog_refresh_response(result)


@router.get("/status")
def get_status() -> AdminStatusResponse:
    config = _load_config()
    return AdminStatusResponse(
        api_version=APP_VERSION,
        redis=_redis_health(),
        metric_count=len(get_loaded_metric_names()),
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
        configured_worker_count=len(config.workers),
        job_store_ttl_seconds=config.job_store.ttl_seconds,
        catalog_fingerprint=get_loaded_catalog_fingerprint(),
    )


@router.get("/config-summary")
def get_config_summary() -> ConfigSummaryResponse:
    config = _load_config()
    return ConfigSummaryResponse(
        api_host=config.api.host,
        api_port=config.api.port,
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
        workers=[
            _worker_config_summary(config, worker_name)
            for worker_name in sorted(config.workers)
        ],
        job_store_ttl_seconds=config.job_store.ttl_seconds,
        plugin_catalog_dir=str(config.plugins.catalog_dir),
        plugin_state_path=str(get_plugin_state_path()),
        plugin_runner_base_dir=str(config.plugins.runner_base_dir),
    )


@router.get("/catalog")
def get_catalog() -> CatalogSummaryResponse:
    config = _load_config()
    store = _state_store(config)
    state = _load_state(store)
    metric_names = get_loaded_metric_names()
    return CatalogSummaryResponse(
        metric_count=len(metric_names),
        metric_names=metric_names,
        catalog_fingerprint=get_loaded_catalog_fingerprint(),
        plugin_sources=[_plugin_source_summary(repo) for repo in state.repos],
        metric_queues=get_loaded_metric_queues()
        or dict(sorted(state.metric_queues.items())),
    )


@router.get("/workers")
def list_workers() -> WorkersResponse:
    config = _load_config()
    snapshot = get_worker_inspect_snapshot()
    return WorkersResponse(
        inspect_available=snapshot.inspect_available,
        workers=[
            _worker_summary(config, snapshot, worker_name)
            for worker_name in _all_worker_names(config, snapshot)
        ],
    )


@router.get("/workers/{worker_name}")
def get_worker(worker_name: str) -> WorkerDetail:
    config = _load_config()
    snapshot = get_worker_inspect_snapshot()
    if (
        worker_name not in config.workers
        and worker_name not in snapshot.observed_worker_names
    ):
        raise HTTPException(status_code=404, detail=f"Unknown worker: {worker_name}")
    return _worker_detail(config, snapshot, worker_name)


@router.get("/queues")
def list_queues() -> QueuesResponse:
    config = _load_config()
    store = _state_store(config)
    state = _load_state(store)
    snapshot = get_worker_inspect_snapshot()
    metric_counts = dict.fromkeys(config.plugins.allowed_queues, 0)
    for queue in state.metric_queues.values():
        metric_counts[queue] = metric_counts.get(queue, 0) + 1

    configured_consumers: dict[str, list[str]] = {
        queue: [] for queue in config.plugins.allowed_queues
    }
    for worker_name, worker in config.workers.items():
        for queue in worker.queues:
            configured_consumers.setdefault(queue, []).append(worker_name)

    observed_consumers: dict[str, list[str]] = {
        queue: [] for queue in config.plugins.allowed_queues
    }
    for worker_name, queues in (snapshot.active_queues or {}).items():
        for queue in queues:
            observed_consumers.setdefault(queue, []).append(worker_name)

    return QueuesResponse(
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
        queues=[
            QueueSummary(
                name=queue,
                is_default=queue == config.plugins.default_queue,
                assigned_metric_count=metric_counts.get(queue, 0),
                configured_workers=sorted(configured_consumers.get(queue, [])),
                observed_workers=sorted(observed_consumers.get(queue, [])),
                pending_depth=None,
                pending_depth_unknown=True,
            )
            for queue in config.plugins.allowed_queues
        ],
    )


@router.post("/workers/restart")
def restart_workers(
    timeout: Annotated[float, _TIMEOUT_QUERY] = 30.0,
) -> WorkerRestartResponse:
    graceful_worker_restart(timeout=timeout)
    return WorkerRestartResponse(
        requested=True,
        timeout=timeout,
        message="Worker restart requested.",
    )


@router.get("/jobs")
def list_jobs(
    limit: Annotated[int, _JOB_LIMIT_QUERY] = 50,
    status: JobLifecycleStatus | None = None,
    metric: str | None = None,
) -> JobListResponse:
    try:
        snapshots = job_store.list_job_statuses(
            limit=limit,
            status=status,
            metric=metric,
        )
    except RedisError as exc:
        raise _redis_unavailable_error() from exc
    return JobListResponse(
        jobs=[
            JobStatusInfo.model_validate(snapshot.model_dump(mode="json"))
            for snapshot in snapshots
        ]
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> JobCancelResponse:
    try:
        snapshot, cancelled = job_store.cancel_job(job_id)
    except RedisError as exc:
        raise _redis_unavailable_error() from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"Job is already terminal: {snapshot.status}",
        )
    revoke_job(job_id)
    return JobCancelResponse(
        job_id=snapshot.job_id,
        status="cancelled",
        cancellation_requested=True,
        revoke_requested=True,
    )


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
