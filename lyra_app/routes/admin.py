"""Administrative HTTP endpoints for managing a Lyra deployment."""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lyra.sdk.config import PluginRepoConfig
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    JobCancelResponse,
    JobLifecycleStatus,
    JobListResponse,
    JobStatusInfo,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    PluginSourceSummary,
    QueuesResponse,
    QueueSummary,
    RedisHealth,
    WorkerConfigSummary,
    WorkerDetail,
    WorkerInspectMetadata,
    WorkersResponse,
    WorkerSummary,
    WorkerTaskSummary,
)
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.config import ConfigLoadError, ConfigSecretError, LyraConfig, get_config
from lyra_app.registry import (
    catalog_error,
    get_loaded_catalog_fingerprint,
    get_loaded_metric_names,
    get_loaded_metric_queues,
    is_catalog_loaded,
    resolved_source_refs,
)
from lyra_app.version import APP_VERSION
from lyra_app.worker_control import (
    WorkerInspectSnapshot,
    WorkerInspectState,
    get_worker_inspect_state,
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


def _load_config() -> LyraConfig:
    try:
        return get_config()
    except ConfigLoadError as exc:
        raise HTTPException(
            status_code=500,
            detail="Lyra config is not configured on the server.",
        ) from exc


def _repo_response(repo: PluginRepoConfig) -> PluginRepoResponse:
    return PluginRepoResponse(
        id=repo.id,
        source=repo.source,
        ref=repo.ref,
        enabled=repo.enabled,
        resolved_ref=resolved_source_refs().get(repo.id),
    )


def _redis_health() -> RedisHealth:
    try:
        pong = job_store.redis_client_sync.ping()
    except RedisError:
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


def _worker_config_summary(config: LyraConfig, worker_name: str) -> WorkerConfigSummary:
    worker = config.get_worker(worker_name)
    return WorkerConfigSummary(
        name=worker_name,
        queues=worker.queues,
        concurrency=worker.concurrency,
        install_dir=str(config.worker_install_dir(worker_name)),
        temp_dir=str(config.worker_temp_dir(worker_name)),
    )


def _plugin_source_summary(repo: PluginRepoConfig) -> PluginSourceSummary:
    return PluginSourceSummary(
        id=repo.id,
        source=repo.source,
        source_kind=repo.source_kind,
        ref=repo.ref,
        enabled=repo.enabled,
        resolved_ref=resolved_source_refs().get(repo.id),
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
    """List all configured plugin repository records.

    Returns:
        Repository metadata in persisted order.
    """
    config = _load_config()
    return PluginRepoListResponse(
        repos=[_repo_response(repo) for repo in config.plugins.repos]
    )


@router.get("/status")
def get_status() -> AdminStatusResponse:
    """Summarize deployment health and active runtime configuration.

    Returns:
        API, Redis, catalog, queue, worker, and retention status.
    """
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
        catalog_available=is_catalog_loaded(),
        catalog_error=catalog_error(),
    )


@router.get("/config-summary")
def get_config_summary() -> ConfigSummaryResponse:
    """Expose a non-secret summary of the validated runtime configuration.

    Returns:
        Listener, queue, worker, retention, and plugin-path settings.
    """
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
        plugin_runner_base_dir=str(config.plugins.runner_base_dir),
    )


@router.get("/catalog")
def get_catalog() -> CatalogSummaryResponse:
    """Summarize the loaded catalog, plugin sources, and metric routing.

    Returns:
        Catalog identity, metric names, sources, and effective queue assignments.
    """
    config = _load_config()
    metric_names = get_loaded_metric_names()
    return CatalogSummaryResponse(
        metric_count=len(metric_names),
        metric_names=metric_names,
        catalog_fingerprint=get_loaded_catalog_fingerprint(),
        catalog_available=is_catalog_loaded(),
        catalog_error=catalog_error(),
        plugin_sources=[_plugin_source_summary(repo) for repo in config.plugins.repos],
        metric_queues=get_loaded_metric_queues(),
    )


@router.get("/workers")
def list_workers() -> WorkersResponse:
    """List configured and observed workers from the latest inspect snapshot.

    Returns:
        Worker summaries and freshness metadata for the background inspection.
    """
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
    """Return task, queue, and runtime details for one worker pool.

    Returns:
        The worker summary, tasks, statistics, and inspection metadata.

    Raises:
        HTTPException: If the worker is neither configured nor observed.
    """
    config = _load_config()
    state = get_worker_inspect_state()
    snapshot = state.snapshot
    if worker_name not in _all_worker_names(config, snapshot):
        raise HTTPException(status_code=404, detail=f"Unknown worker: {worker_name}")
    return _worker_detail(config, snapshot, worker_name, _inspect_metadata(state))


@router.get("/queues")
def list_queues() -> QueuesResponse:
    """Summarize metric assignments and worker consumers for allowed queues.

    Returns:
        Queue assignment counts and configured and observed consumers.
    """
    config = _load_config()
    inspect_state = get_worker_inspect_state()
    snapshot = inspect_state.snapshot
    metric_counts = dict.fromkeys(config.plugins.allowed_queues, 0)
    for queue in get_loaded_metric_queues().values():
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
        catalog_available=is_catalog_loaded(),
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


@router.get("/jobs")
def list_jobs(
    limit: Annotated[int, _JOB_LIMIT_QUERY] = 50,
    status: JobLifecycleStatus | None = None,
    metric: str | None = None,
) -> JobListResponse:
    """List recent retained jobs with optional lifecycle and metric filters.

    Returns:
        Matching jobs in reverse chronological order.

    Raises:
        HTTPException: If Redis is unavailable.
    """
    try:
        snapshots = job_store.list_job_statuses(
            limit=limit,
            status=status,
            metric=metric,
        )
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Redis. Please try again later.",
        ) from exc
    return JobListResponse(
        jobs=[
            JobStatusInfo.model_validate(snapshot.model_dump(mode="json"))
            for snapshot in snapshots
        ]
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> JobCancelResponse:
    """Cancel a nonterminal job and request Celery task revocation.

    Returns:
        Confirmation that the Lyra state and Celery revocation were requested.

    Raises:
        HTTPException: If the job is absent or already terminal.
    """
    try:
        snapshot, cancelled = job_store.cancel_job(job_id)
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Redis. Please try again later.",
        ) from exc
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
    """Return persisted metric queue assignments and queue defaults.

    Returns:
        Explicit metric routes, allowed queues, and the default queue.
    """
    config = _load_config()
    return PluginRoutingResponse(
        metric_queues=get_loaded_metric_queues(),
        overrides={repo.id: repo.routing for repo in config.plugins.repos},
        disabled_repos=[repo.id for repo in config.plugins.repos if not repo.enabled],
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
    )
