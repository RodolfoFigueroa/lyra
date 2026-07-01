from datetime import datetime
from typing import Any, Literal

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field

ReadinessStatus = Literal["ok", "unavailable", "unknown"]
InstanceStatus = Literal["ok", "degraded"]
WorkerObservedStatus = Literal["online", "offline", "unknown"]


class RedisHealth(StrictBaseModel):
    status: ReadinessStatus = Field(description="Redis readiness state.")


class HealthResponse(StrictBaseModel):
    status: InstanceStatus = Field(description="Overall API readiness state.")
    api_version: str = Field(min_length=1, description="Running Lyra API version.")
    redis: RedisHealth = Field(description="Redis readiness details.")


class WorkerConfigSummary(StrictBaseModel):
    name: str = Field(min_length=1)
    queues: list[str] = Field(min_length=1)
    concurrency: int = Field(gt=0)
    install_dir: str
    temp_dir: str


class ConfigSummaryResponse(StrictBaseModel):
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
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_kind: Literal["github", "local", "directory"]
    ref: str | None = None
    enabled: bool


class CatalogSummaryResponse(StrictBaseModel):
    metric_count: int = Field(ge=0)
    metric_names: list[str]
    catalog_fingerprint: str
    plugin_sources: list[PluginSourceSummary]
    metric_queues: dict[str, str]


class WorkerTaskSummary(StrictBaseModel):
    id: str | None = None
    name: str | None = None
    worker: str | None = None
    eta: str | None = None
    time_start: float | None = None


class WorkerSummary(StrictBaseModel):
    name: str = Field(min_length=1)
    configured: bool
    observed: bool
    status: WorkerObservedStatus
    queues: list[str]
    active_count: int | None = None
    reserved_count: int | None = None
    scheduled_count: int | None = None


class WorkerInspectMetadata(StrictBaseModel):
    observed_at: datetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    stale: bool = True
    last_error: str | None = None


class WorkerDetail(WorkerSummary):
    active_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    reserved_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    scheduled_tasks: list[WorkerTaskSummary] = Field(default_factory=list)
    stats: dict[str, Any] | None = None
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )


class WorkersResponse(StrictBaseModel):
    inspect_available: bool
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )
    workers: list[WorkerSummary]


class QueueSummary(StrictBaseModel):
    name: str = Field(min_length=1)
    is_default: bool
    assigned_metric_count: int = Field(ge=0)
    configured_workers: list[str]
    observed_workers: list[str]
    pending_depth: int | None = None
    pending_depth_unknown: bool = True


class QueuesResponse(StrictBaseModel):
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    inspect_metadata: WorkerInspectMetadata = Field(
        default_factory=WorkerInspectMetadata
    )
    queues: list[QueueSummary]


class AdminStatusResponse(StrictBaseModel):
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
    "HealthResponse",
    "InstanceStatus",
    "PluginSourceSummary",
    "QueueSummary",
    "QueuesResponse",
    "ReadinessStatus",
    "RedisHealth",
    "WorkerConfigSummary",
    "WorkerDetail",
    "WorkerInspectMetadata",
    "WorkerObservedStatus",
    "WorkerSummary",
    "WorkerTaskSummary",
    "WorkersResponse",
]
