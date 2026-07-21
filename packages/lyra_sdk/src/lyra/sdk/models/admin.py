"""Models for administrative status and control operations."""

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class PluginRepoResponse(StrictBaseModel):
    """Configured plugin repository details."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    ref: str | None = None
    enabled: bool


class PluginRepoListResponse(StrictBaseModel):
    """Collection of configured plugin repositories."""

    repos: list[PluginRepoResponse]


class CreatePluginRepoRequest(StrictBaseModel):
    """Values accepted when registering a plugin repository."""

    source: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    enabled: bool = True


class UpdatePluginRepoRequest(StrictBaseModel):
    """Mutable settings for a registered plugin repository."""

    source: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class PluginCatalogRefreshStatus(StrictBaseModel):
    """Catalog refresh outcome attached to a repository operation."""

    refreshed: bool
    error: str | None = None
    catalog_changed: bool | None
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str | None
    assigned_metric_queues: list[str]
    removed_metric_queues: list[str]
    workers_restart_recommended: bool


class CreatePluginRepoResponse(StrictBaseModel):
    """New repository and its resulting catalog refresh status."""

    repo: PluginRepoResponse
    catalog_refresh: PluginCatalogRefreshStatus


class UpdatePluginRepoResponse(StrictBaseModel):
    """Updated repository and its resulting catalog refresh status."""

    repo: PluginRepoResponse
    catalog_refresh: PluginCatalogRefreshStatus


class DeletePluginRepoResponse(StrictBaseModel):
    """Repository deletion outcome and affected metric queues."""

    deleted: bool
    repo_id: str = Field(min_length=1)
    removed_metric_queues: list[str]
    catalog_refresh: PluginCatalogRefreshStatus


class SyncPluginRepoResponse(StrictBaseModel):
    """Repository synchronization and catalog refresh outcome."""

    repo_id: str = Field(min_length=1)
    changed: bool
    display_name: str = Field(min_length=1)
    catalog_refresh: PluginCatalogRefreshStatus


class PluginCatalogRefreshResponse(StrictBaseModel):
    """Detailed result of an explicit plugin catalog refresh."""

    updated_plugins: list[str]
    catalog_changed: bool
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str = Field(min_length=1)
    assigned_metric_queues: list[str]
    removed_metric_queues: list[str]
    workers_restarted: bool
    workers_restart_recommended: bool
    message: str = Field(min_length=1)


class WorkerRestartResponse(StrictBaseModel):
    """Result of requesting a worker restart."""

    requested: bool
    timeout: float = Field(ge=0.0)
    message: str = Field(min_length=1)


class PluginRoutingResponse(StrictBaseModel):
    """Current metric-to-queue routing configuration."""

    metric_queues: dict[str, str]
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)


class SetMetricQueueRequest(StrictBaseModel):
    """Queue selected for a metric routing assignment."""

    queue: str = Field(min_length=1)


class MetricQueueAssignmentResponse(StrictBaseModel):
    """Confirmed metric-to-queue routing assignment."""

    metric_name: str = Field(min_length=1)
    queue: str = Field(min_length=1)


class DeleteMetricQueueResponse(StrictBaseModel):
    """Result of removing a metric queue assignment."""

    deleted: bool
    metric_name: str = Field(min_length=1)


__all__ = [
    "CreatePluginRepoRequest",
    "CreatePluginRepoResponse",
    "DeleteMetricQueueResponse",
    "DeletePluginRepoResponse",
    "MetricQueueAssignmentResponse",
    "PluginCatalogRefreshResponse",
    "PluginCatalogRefreshStatus",
    "PluginRepoListResponse",
    "PluginRepoResponse",
    "PluginRoutingResponse",
    "SetMetricQueueRequest",
    "SyncPluginRepoResponse",
    "UpdatePluginRepoRequest",
    "UpdatePluginRepoResponse",
    "WorkerRestartResponse",
]
