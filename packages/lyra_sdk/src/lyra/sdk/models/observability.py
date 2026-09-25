"""Models for service health, readiness, and runtime observations."""

from datetime import datetime
from typing import Literal

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
    catalog_available: bool = Field(
        description="Whether startup catalog initialization succeeded."
    )
    catalog_error: str | None = None


class WorkerConfigSummary(StrictBaseModel):
    """Configured worker process settings."""

    name: str = Field(min_length=1)
    queues: list[str] = Field(min_length=1)
    concurrency: int = Field(gt=0)
    temp_dir: str


class ConfigSummaryResponse(StrictBaseModel):
    """Effective service and worker configuration summary."""

    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    workers: list[WorkerConfigSummary]
    result_retention_seconds: int = Field(gt=0)


class InstalledPluginSummary(StrictBaseModel):
    """Configured installed plugin metadata."""

    distribution: str = Field(min_length=1)
    version: str | None = None
    enabled: bool


class CatalogSummaryResponse(StrictBaseModel):
    """Plugin catalog contents and routing summary."""

    metric_count: int = Field(ge=0)
    metric_names: list[str]
    catalog_fingerprint: str
    catalog_available: bool
    catalog_error: str | None = None
    installed_plugins: list[InstalledPluginSummary]
    metric_queues: dict[str, str]


class WorkerSummary(StrictBaseModel):
    """Native RQ worker process and heartbeat observation."""

    name: str
    hostname: str
    queues: list[str]
    last_heartbeat: datetime | None = None
    heartbeat_age_seconds: float | None = None
    stale: bool
    current_job: str | None = None
    state: str


class WorkerDetail(WorkerSummary):
    """Native worker process detail."""

    pid: int | None = None


class WorkersResponse(StrictBaseModel):
    """Configured pools separately from observed native worker processes."""

    pools: list[WorkerConfigSummary]
    workers: list[WorkerDetail]


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
    catalog_available: bool
    queues: list[QueueSummary]


class AdminStatusResponse(StrictBaseModel):
    """High-level operational status for the Lyra service."""

    api_version: str = Field(min_length=1)
    redis: RedisHealth
    metric_count: int = Field(ge=0)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
    configured_worker_count: int = Field(ge=0)
    result_retention_seconds: int = Field(gt=0)
    catalog_fingerprint: str
    catalog_available: bool
    catalog_error: str | None = None
