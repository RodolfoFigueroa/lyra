"""Models for service health, readiness, and runtime observations."""

from datetime import datetime
from typing import Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

ReadinessStatus = Literal["ok", "unavailable", "unknown"]
ServiceReadinessStatus = Literal["ready", "not_ready"]
WorkerObservedStatus = Literal["online", "offline", "unknown"]


class RedisHealth(StrictBaseModel):
    """Redis readiness observation."""

    status: ReadinessStatus = Field(description="Redis readiness state.")


class DatabaseHealth(StrictBaseModel):
    """PostgreSQL readiness observation."""

    status: ReadinessStatus = Field(description="PostgreSQL readiness state.")


class LivenessResponse(StrictBaseModel):
    """API process liveness response."""

    status: Literal["ok"] = Field(description="API process liveness state.")
    api_version: str = Field(min_length=1, description="Running Lyra API version.")


class ReadinessResponse(StrictBaseModel):
    """Aggregate readiness response for the API and its dependencies."""

    status: ServiceReadinessStatus = Field(description="Overall API readiness state.")
    api_version: str = Field(min_length=1, description="Running Lyra API version.")
    redis: RedisHealth = Field(description="Redis readiness details.")
    database: DatabaseHealth = Field(description="PostgreSQL readiness details.")


class WorkerConfigSummary(StrictBaseModel):
    """Configured worker process settings."""

    name: str = Field(min_length=1)
    queues: list[str] = Field(min_length=1)
    concurrency: int = Field(gt=0)
    install_dir: str
    temp_dir: str


class ConfigSummaryResponse(StrictBaseModel):
    """Effective service and worker configuration summary."""

    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    workers: list[WorkerConfigSummary]
    job_store_ttl_seconds: int = Field(gt=0)
    plugin_catalog_dir: str
    plugin_state_path: str
    plugin_runner_base_dir: str


class PluginSourceSummary(StrictBaseModel):
    """Configured source of plugin definitions."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_kind: Literal["github", "local", "directory"]
    ref: str | None = None
    enabled: bool


class CatalogSummaryResponse(StrictBaseModel):
    """Plugin catalog contents and routing summary."""

    metric_count: int = Field(ge=0)
    metric_names: list[str]
    catalog_fingerprint: str
    plugin_sources: list[PluginSourceSummary]
    metric_queues: dict[str, str]


class WorkerTaskSummary(StrictBaseModel):
    """Task observed by Celery worker inspection."""

    id: str | None = None
    name: str | None = None
    worker: str | None = None
    eta: str | None = None
    time_start: float | None = None


class WorkerSummary(StrictBaseModel):
    """Configured and observed state of a worker."""

    name: str = Field(min_length=1)
    configured: bool
    observed: bool
    status: WorkerObservedStatus
    queues: list[str]
    active_count: int | None = None
    reserved_count: int | None = None
    scheduled_count: int | None = None


class WorkerInspectMetadata(StrictBaseModel):
    """Freshness and availability metadata for worker inspection."""

    observed_at: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    stale: bool = True
    last_error: str | None = None


class WorkerDetail(WorkerSummary):
    """Worker state with inspected tasks and runtime statistics."""

    active_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    reserved_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    scheduled_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    stats: dict[str, Any] | None = None
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )


class WorkersResponse(StrictBaseModel):
    """Worker summaries and inspection availability."""

    inspect_available: bool
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )
    workers: list[WorkerSummary]


class QueueSummary(StrictBaseModel):
    """Configured routing and observed capacity for a queue."""

    name: str = Field(min_length=1)
    is_default: bool
    assigned_metric_count: int = Field(ge=0)
    configured_workers: list[str]
    observed_workers: list[str]
    pending_depth: int | None = None
    pending_depth_unknown: bool = True


class QueuesResponse(StrictBaseModel):
    """Queue summaries and their worker-inspection metadata."""

    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )
    queues: list[QueueSummary]


class AdminStatusResponse(StrictBaseModel):
    """High-level operational status for the Lyra service."""

    api_version: str = Field(min_length=1)
    redis: RedisHealth
    metric_count: int = Field(ge=0)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    configured_worker_count: int = Field(ge=0)
    job_store_ttl_seconds: int = Field(gt=0)
    catalog_fingerprint: str


__all__ = [
    "AdminStatusResponse",
    "CatalogSummaryResponse",
    "ConfigSummaryResponse",
    "DatabaseHealth",
    "LivenessResponse",
    "PluginSourceSummary",
    "QueueSummary",
    "QueuesResponse",
    "ReadinessResponse",
    "ReadinessStatus",
    "RedisHealth",
    "ServiceReadinessStatus",
    "WorkerConfigSummary",
    "WorkerDetail",
    "WorkerInspectMetadata",
    "WorkerObservedStatus",
    "WorkerSummary",
    "WorkerTaskSummary",
    "WorkersResponse",
]
