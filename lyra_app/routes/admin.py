"""Administrative HTTP endpoints for managing a Lyra deployment."""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from lyra.sdk.config import InstalledPluginConfig
from lyra.sdk.models.admin import (
    InstalledPluginListResponse,
    InstalledPluginResponse,
    PluginRoutingResponse,
)
from lyra.sdk.models.job import (
    AdminJobDetail,
    JobLifecycleStatus,
    JobListResponse,
)
from lyra.sdk.models.observability import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    InstalledPluginSummary,
    QueuesResponse,
    QueueSummary,
    RedisHealth,
    WorkerConfigSummary,
    WorkerDetail,
    WorkersResponse,
)
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.serializers import JSONSerializer
from rq.utils import as_text

from lyra_app import job_store
from lyra_app.config import ConfigLoadError, ConfigSecretError, LyraConfig, get_config
from lyra_app.db.redis import get_sync_client
from lyra_app.plugins import installed_version
from lyra_app.registry import (
    catalog_error,
    get_loaded_catalog_fingerprint,
    get_loaded_metric_names,
    get_loaded_metric_queues,
    is_catalog_loaded,
)
from lyra_app.version import APP_VERSION

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


def _load_config() -> LyraConfig:
    try:
        return get_config()
    except ConfigLoadError as exc:
        raise HTTPException(
            status_code=500,
            detail="Lyra config is not configured on the server.",
        ) from exc


def _plugin_response(plugin: InstalledPluginConfig) -> InstalledPluginResponse:
    return InstalledPluginResponse(
        distribution=plugin.distribution,
        version=installed_version(plugin.distribution),
        enabled=plugin.enabled,
    )


def _redis_health() -> RedisHealth:
    try:
        pong = get_sync_client().ping()
    except RedisError:
        return RedisHealth(status="unavailable")
    return RedisHealth(status="ok" if pong else "unavailable")


def _worker_config_summary(config: LyraConfig, worker_name: str) -> WorkerConfigSummary:
    worker = config.get_worker(worker_name)
    return WorkerConfigSummary(
        name=worker_name,
        queues=worker.queues,
        concurrency=worker.concurrency,
        temp_dir=str(config.worker_temp_dir(worker_name)),
    )


def _installed_plugin_summary(plugin: InstalledPluginConfig) -> InstalledPluginSummary:
    return InstalledPluginSummary(
        distribution=plugin.distribution,
        version=installed_version(plugin.distribution),
        enabled=plugin.enabled,
    )


def _observed_workers() -> list[WorkerDetail]:
    # Worker.all() removes stale registry entries and constructs worker connections.
    # Read hashes directly so observation neither maintains registries nor changes
    # the API connection's timeout to a worker's blocking-dequeue timeout.
    connection = get_sync_client()
    now = datetime.now(UTC)
    workers = []
    for key in Worker.all_keys(connection=connection):
        raw = connection.hgetall(key)
        if not raw:
            continue
        data = {as_text(field): as_text(value) for field, value in raw.items()}
        heartbeat = (
            datetime.fromisoformat(data["last_heartbeat"])
            if data.get("last_heartbeat")
            else None
        )
        workers.append(
            WorkerDetail(
                name=key.removeprefix(Worker.redis_worker_namespace_prefix),
                hostname=data.get("hostname", ""),
                queues=data["queues"].split(",") if data.get("queues") else [],
                last_heartbeat=heartbeat,
                heartbeat_age_seconds=max(0, (now - heartbeat).total_seconds())
                if heartbeat
                else None,
                stale=heartbeat is None or (now - heartbeat).total_seconds() > 30,
                current_job=data.get("current_job") or None,
                state=data.get("state", "?"),
                pid=int(data["pid"]) if data.get("pid") else None,
            )
        )
    return workers


@router.get("/plugins")
def list_plugins() -> InstalledPluginListResponse:
    """List configured installed plugins.

    Returns:
        Installed distribution metadata in configuration order.
    """
    config = _load_config()
    return InstalledPluginListResponse(
        plugins=[_plugin_response(plugin) for plugin in config.plugins.installed]
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
        result_retention_seconds=config.jobs.result_retention_seconds,
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
        result_retention_seconds=config.jobs.result_retention_seconds,
    )


@router.get("/catalog")
def get_catalog() -> CatalogSummaryResponse:
    """Summarize the loaded catalog, installed plugins, and metric routing.

    Returns:
        Catalog identity, metric names, distributions, and effective queues.
    """
    config = _load_config()
    metric_names = get_loaded_metric_names()
    return CatalogSummaryResponse(
        metric_count=len(metric_names),
        metric_names=metric_names,
        catalog_fingerprint=get_loaded_catalog_fingerprint(),
        catalog_available=is_catalog_loaded(),
        catalog_error=catalog_error(),
        installed_plugins=[
            _installed_plugin_summary(plugin) for plugin in config.plugins.installed
        ],
        metric_queues=get_loaded_metric_queues(),
    )


@router.get("/workers")
def list_workers() -> WorkersResponse:
    """Return configured pools and native worker processes separately."""
    config = _load_config()
    return WorkersResponse(
        pools=[_worker_config_summary(config, name) for name in sorted(config.workers)],
        workers=_observed_workers(),
    )


@router.get("/workers/{worker_name}")
def get_worker(worker_name: str) -> WorkerDetail:
    """Return a native worker process by ID.

    Raises:
        HTTPException: If the worker is no longer registered.
    """
    for worker in _observed_workers():
        if worker.name == worker_name:
            return worker
    raise HTTPException(status_code=404, detail="Worker not found")


@router.get("/queues")
def list_queues() -> QueuesResponse:
    """Return configured routing and native queue depth."""
    config = _load_config()
    workers = _observed_workers()
    metric_queues = get_loaded_metric_queues()
    return QueuesResponse(
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
        catalog_available=is_catalog_loaded(),
        queues=[
            QueueSummary(
                name=queue,
                is_default=queue == config.plugins.default_queue,
                assigned_metric_count=list(metric_queues.values()).count(queue),
                configured_workers=[
                    name
                    for name, pool in config.workers.items()
                    if queue in pool.queues
                ],
                observed_workers=[
                    worker.name
                    for worker in workers
                    if queue in worker.queues and not worker.stale
                ],
                pending_depth=Queue(
                    queue, connection=get_sync_client(), serializer=JSONSerializer
                ).count,
                pending_depth_unknown=False,
            )
            for queue in config.plugins.allowed_queues
        ],
    )


@router.get("/jobs")
def list_jobs(
    queue: str,
    status: JobLifecycleStatus,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobListResponse:
    """Read one native queue/status page without registry cleanup.

    Returns:
        Retained jobs from the requested native page.

    Raises:
        HTTPException: If the queue is not configured.
    """
    if queue not in _load_config().plugins.allowed_queues:
        raise HTTPException(status_code=422, detail="Queue is not configured")
    return JobListResponse(
        jobs=job_store.list_job_statuses(
            queue=queue,
            status=status,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> AdminJobDetail:
    """Return native job diagnostics to authenticated administrators.

    Raises:
        HTTPException: If the job expired or does not exist.
    """
    detail = job_store.get_job_detail(job_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Job expired or not found")
    return detail


@router.get("/plugin-routing")
def list_plugin_routing() -> PluginRoutingResponse:
    """Return persisted metric queue assignments and queue defaults.

    Returns:
        Explicit metric routes, allowed queues, and the default queue.
    """
    config = _load_config()
    return PluginRoutingResponse(
        metric_queues=get_loaded_metric_queues(),
        overrides={
            plugin.distribution: plugin.routing for plugin in config.plugins.installed
        },
        disabled_plugins=[
            plugin.distribution
            for plugin in config.plugins.installed
            if not plugin.enabled
        ],
        allowed_queues=config.plugins.allowed_queues,
        default_queue=config.plugins.default_queue,
    )
