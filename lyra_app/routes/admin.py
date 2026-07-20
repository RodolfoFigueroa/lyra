import hmac
import logging
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
    CreatePluginRepoResponse,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    JobCancelResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    MetricQueueAssignmentResponse,
    PluginCatalogRefreshResponse,
    PluginCatalogRefreshStatus,
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
    UpdatePluginRepoResponse,
    WorkerConfigSummary,
    WorkerDetail,
    WorkerInspectMetadata,
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
    metric_queue_mapping,
    normalize_repo_source,
    repo_record_to_source,
)
from lyra_app.plugins import (
    PluginSyncError,
    format_update_message,
    remove_plugin_snapshot,
)
from lyra_app.plugins import (
    sync_plugin_repo as sync_plugin_source,
)
from lyra_app.registry import (
    CatalogRefreshResult,
    get_loaded_catalog_fingerprint,
    get_loaded_metric_names,
    get_loaded_metric_queues,
    get_metric_entry,
    refresh_catalog_from_state,
    reset_catalog,
)
from lyra_app.version import APP_VERSION
from lyra_app.worker_control import (
    WorkerInspectSnapshot,
    WorkerInspectState,
    get_worker_inspect_state,
    graceful_worker_restart,
    revoke_job,
    safe_task_summary,
)

_bearer = HTTPBearer(scheme_name="AdminBearer")
logger = logging.getLogger(__name__)


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


router = APIRouter(
    prefix="/admin",
    tags=["Administration"],
    dependencies=[Depends(require_admin_key)],
)


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


def _catalog_refresh_error_detail(exc: Exception) -> str:
    if isinstance(exc, (PluginSyncError, subprocess.CalledProcessError)):
        return _sync_error_detail(exc)
    return str(exc)


def _remove_repo_snapshot(config: LyraConfig, repo: PluginRepoRecord) -> None:
    try:
        remove_plugin_snapshot(config.plugins.catalog_dir, repo_record_to_source(repo))
    except (OSError, ValueError) as exc:
        logger.warning(
            "Failed to remove managed plugin snapshot for repo %s: %s",
            repo.id,
            exc,
        )


def _catalog_restart_recommended(result: CatalogRefreshResult) -> bool:
    return bool(
        result.updated_plugins
        or result.catalog_changed
        or result.assigned_metric_queues
        or result.removed_metric_queues
    )


def _catalog_refresh_status_from_result(
    result: CatalogRefreshResult,
) -> PluginCatalogRefreshStatus:
    return PluginCatalogRefreshStatus(
        refreshed=True,
        error=None,
        catalog_changed=result.catalog_changed,
        previous_catalog_fingerprint=result.previous_catalog_fingerprint,
        catalog_fingerprint=result.catalog_fingerprint,
        assigned_metric_queues=result.assigned_metric_queues,
        removed_metric_queues=result.removed_metric_queues,
        workers_restart_recommended=_catalog_restart_recommended(result),
    )


def _catalog_refresh_failure_status(exc: Exception) -> PluginCatalogRefreshStatus:
    return PluginCatalogRefreshStatus(
        refreshed=False,
        error=_catalog_refresh_error_detail(exc),
        catalog_changed=None,
        previous_catalog_fingerprint=None,
        catalog_fingerprint=None,
        assigned_metric_queues=[],
        removed_metric_queues=[],
        workers_restart_recommended=False,
    )


def _refresh_catalog_status(store: PluginStateStore) -> PluginCatalogRefreshStatus:
    try:
        result = refresh_catalog_from_state(store)
    except (
        PluginSyncError,
        subprocess.CalledProcessError,
        PluginStateLoadError,
        PluginStateValidationError,
        RuntimeError,
    ) as exc:
        reset_catalog()
        return _catalog_refresh_failure_status(exc)
    return _catalog_refresh_status_from_result(result)


def _catalog_refresh_response(
    result: CatalogRefreshResult,
) -> PluginCatalogRefreshResponse:
    restart_recommended = _catalog_restart_recommended(result)
    return PluginCatalogRefreshResponse(
        updated_plugins=result.updated_plugins,
        catalog_changed=result.catalog_changed,
        previous_catalog_fingerprint=result.previous_catalog_fingerprint,
        catalog_fingerprint=result.catalog_fingerprint,
        assigned_metric_queues=result.assigned_metric_queues,
        removed_metric_queues=result.removed_metric_queues,
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


def _inspect_metadata(state: WorkerInspectState) -> WorkerInspectMetadata:
    return WorkerInspectMetadata(
        observed_at=state.observed_at,
        age_seconds=state.age_seconds,
        stale=state.stale,
        last_error=state.last_error,
    )


def _task_summaries(
    section: dict[str, list[dict[str, Any]]] | None,
    config: LyraConfig,
    worker_name: str,
) -> list[WorkerTaskSummary]:
    if section is None:
        return []
    summaries: list[WorkerTaskSummary] = []
    for observed_worker_name, tasks in sorted(section.items()):
        if _response_worker_name(config, observed_worker_name) != worker_name:
            continue
        summaries.extend(
            WorkerTaskSummary.model_validate(
                safe_task_summary(task, worker_name=worker_name)
            )
            for task in tasks
        )
    return summaries


def _task_count(
    section: dict[str, list[dict[str, Any]]] | None,
    config: LyraConfig,
    worker_name: str,
) -> int | None:
    if section is None:
        return None
    return sum(
        len(tasks)
        for observed_worker_name, tasks in section.items()
        if _response_worker_name(config, observed_worker_name) == worker_name
    )


def _response_worker_name(config: LyraConfig, observed_worker_name: str) -> str:
    if observed_worker_name in config.workers:
        return observed_worker_name
    pool_name, separator, _ = observed_worker_name.partition("@")
    if separator and pool_name in config.workers:
        return pool_name
    return observed_worker_name


def _observed_worker_names(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
) -> set[str]:
    return {
        _response_worker_name(config, observed_worker_name)
        for observed_worker_name in snapshot.observed_worker_names
    }


def _worker_stats(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> dict[str, Any] | None:
    if snapshot.stats is None:
        return None
    matching_stats = {
        observed_worker_name: stats
        for observed_worker_name, stats in snapshot.stats.items()
        if _response_worker_name(config, observed_worker_name) == worker_name
    }
    if not matching_stats:
        return None
    if len(matching_stats) == 1:
        return next(iter(matching_stats.values()))
    return {"nodes": dict(sorted(matching_stats.items()))}


def _worker_queues(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> list[str]:
    queues: set[str] = set()
    if worker_name in config.workers:
        queues.update(config.get_worker(worker_name).queues)
    if snapshot.active_queues is not None:
        for observed_worker_name, worker_queues in snapshot.active_queues.items():
            if _response_worker_name(config, observed_worker_name) == worker_name:
                queues.update(worker_queues)
    return sorted(queues)


def _worker_summary(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
) -> WorkerSummary:
    configured = worker_name in config.workers
    observed = worker_name in _observed_worker_names(config, snapshot)
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
        active_count=_task_count(snapshot.active, config, worker_name),
        reserved_count=_task_count(snapshot.reserved, config, worker_name),
        scheduled_count=_task_count(snapshot.scheduled, config, worker_name),
    )


def _worker_detail(
    config: LyraConfig,
    snapshot: WorkerInspectSnapshot,
    worker_name: str,
    inspect_metadata: WorkerInspectMetadata,
) -> WorkerDetail:
    summary = _worker_summary(config, snapshot, worker_name)
    return WorkerDetail(
        **summary.model_dump(mode="json"),
        active_tasks=_task_summaries(snapshot.active, config, worker_name),
        reserved_tasks=_task_summaries(snapshot.reserved, config, worker_name),
        scheduled_tasks=_task_summaries(snapshot.scheduled, config, worker_name),
        stats=_worker_stats(config, snapshot, worker_name),
        inspect_metadata=inspect_metadata,
    )


def _all_worker_names(config: LyraConfig, snapshot: WorkerInspectSnapshot) -> list[str]:
    return sorted(set(config.workers) | _observed_worker_names(config, snapshot))


@router.get("/plugin-repos")
def list_plugin_repos() -> PluginRepoListResponse:
    store = _state_store()
    state = _load_state(store)
    return PluginRepoListResponse(repos=[_repo_response(repo) for repo in state.repos])


@router.post("/plugin-repos")
def create_plugin_repo(request: CreatePluginRepoRequest) -> CreatePluginRepoResponse:
    store = _state_store()
    try:
        repo = store.add_repo(
            request.source,
            repo_id=request.id,
            enabled=request.enabled,
        )
    except (PluginStateValidationError, ValueError) as exc:
        raise _validation_error(exc) from exc
    return CreatePluginRepoResponse(
        repo=_repo_response(repo),
        catalog_refresh=_refresh_catalog_status(store),
    )


@router.patch("/plugin-repos/{repo_id}")
def update_plugin_repo(
    repo_id: str,
    request: UpdatePluginRepoRequest,
) -> UpdatePluginRepoResponse:
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
    return UpdatePluginRepoResponse(
        repo=_repo_response(repo),
        catalog_refresh=_refresh_catalog_status(store),
    )


@router.delete("/plugin-repos/{repo_id}")
def delete_plugin_repo(repo_id: str) -> DeletePluginRepoResponse:
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

    try:
        result = store.delete_repo(repo_id)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    if not result.deleted:
        raise HTTPException(
            status_code=404,
            detail=f"unknown plugin repo id: {repo_id}",
        )

    _remove_repo_snapshot(config, repo)
    return DeletePluginRepoResponse(
        deleted=True,
        repo_id=repo_id,
        removed_metric_queues=result.removed_metric_queues,
        catalog_refresh=_refresh_catalog_status(store),
    )


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
        catalog_refresh=_refresh_catalog_status(store),
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
        metric_queues=get_loaded_metric_queues() or metric_queue_mapping(state),
    )


@router.get("/workers")
def list_workers() -> WorkersResponse:
    config = _load_config()
    state = get_worker_inspect_state()
    snapshot = state.snapshot
    return WorkersResponse(
        inspect_available=snapshot.inspect_available,
        inspect_metadata=_inspect_metadata(state),
        workers=[
            _worker_summary(config, snapshot, worker_name)
            for worker_name in _all_worker_names(config, snapshot)
        ],
    )


@router.get("/workers/{worker_name}")
def get_worker(worker_name: str) -> WorkerDetail:
    config = _load_config()
    state = get_worker_inspect_state()
    snapshot = state.snapshot
    if worker_name not in _all_worker_names(config, snapshot):
        raise HTTPException(status_code=404, detail=f"Unknown worker: {worker_name}")
    return _worker_detail(config, snapshot, worker_name, _inspect_metadata(state))


@router.get("/queues")
def list_queues() -> QueuesResponse:
    config = _load_config()
    store = _state_store(config)
    state = _load_state(store)
    inspect_state = get_worker_inspect_state()
    snapshot = inspect_state.snapshot
    metric_counts = dict.fromkeys(config.plugins.allowed_queues, 0)
    for queue in metric_queue_mapping(state).values():
        metric_counts[queue] = metric_counts.get(queue, 0) + 1

    configured_consumers: dict[str, set[str]] = {
        queue: set() for queue in config.plugins.allowed_queues
    }
    for worker_name, worker in config.workers.items():
        for queue in worker.queues:
            configured_consumers.setdefault(queue, set()).add(worker_name)

    observed_consumers: dict[str, set[str]] = {
        queue: set() for queue in config.plugins.allowed_queues
    }
    for observed_worker_name, queues in (snapshot.active_queues or {}).items():
        worker_name = _response_worker_name(config, observed_worker_name)
        for queue in queues:
            observed_consumers.setdefault(queue, set()).add(worker_name)

    return QueuesResponse(
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
        inspect_metadata=_inspect_metadata(inspect_state),
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
        metric_queues=metric_queue_mapping(state),
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
    )


@router.put("/plugin-routing/{metric_name}")
def set_plugin_routing(
    metric_name: str,
    request: SetMetricQueueRequest,
) -> MetricQueueAssignmentResponse:
    store = _state_store()
    stripped_metric_name = metric_name.strip()
    entry = get_metric_entry(stripped_metric_name)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metric '{stripped_metric_name}' not found.",
        )
    try:
        queue = store.set_metric_queue(
            stripped_metric_name,
            request.queue,
            repo_id=entry.repo_id,
        )
    except (PluginStateValidationError, ValueError) as exc:
        raise _validation_error(exc) from exc
    return MetricQueueAssignmentResponse(metric_name=stripped_metric_name, queue=queue)


@router.delete("/plugin-routing/{metric_name}")
def delete_plugin_routing(metric_name: str) -> DeleteMetricQueueResponse:
    store = _state_store()
    try:
        deleted = store.delete_metric_queue(metric_name)
    except ValueError as exc:
        raise _validation_error(exc) from exc
    return DeleteMetricQueueResponse(deleted=deleted, metric_name=metric_name.strip())
