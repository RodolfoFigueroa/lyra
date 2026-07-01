from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class PluginRepoResponse(StrictBaseModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    ref: str | None = None
    enabled: bool


class PluginRepoListResponse(StrictBaseModel):
    repos: list[PluginRepoResponse]


class CreatePluginRepoRequest(StrictBaseModel):
    source: str = Field(min_length=1)
    id: str | None = Field(default=None, min_length=1)
    enabled: bool = True


class UpdatePluginRepoRequest(StrictBaseModel):
    source: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class PluginCatalogRefreshStatus(StrictBaseModel):
    refreshed: bool
    error: str | None = None
    catalog_changed: bool | None
    previous_catalog_fingerprint: str | None
    catalog_fingerprint: str | None
    assigned_metric_queues: list[str]
    removed_metric_queues: list[str]
    workers_restart_recommended: bool


class CreatePluginRepoResponse(StrictBaseModel):
    repo: PluginRepoResponse
    catalog_refresh: PluginCatalogRefreshStatus


class UpdatePluginRepoResponse(StrictBaseModel):
    repo: PluginRepoResponse
    catalog_refresh: PluginCatalogRefreshStatus


class DeletePluginRepoResponse(StrictBaseModel):
    deleted: bool
    repo_id: str = Field(min_length=1)
    removed_metric_queues: list[str]
    catalog_refresh: PluginCatalogRefreshStatus


class SyncPluginRepoResponse(StrictBaseModel):
    repo_id: str = Field(min_length=1)
    changed: bool
    display_name: str = Field(min_length=1)
    catalog_refresh: PluginCatalogRefreshStatus


class PluginCatalogRefreshResponse(StrictBaseModel):
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
    requested: bool
    timeout: float = Field(ge=0.0)
    message: str = Field(min_length=1)


class PluginRoutingResponse(StrictBaseModel):
    metric_queues: dict[str, str]
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)


class SetMetricQueueRequest(StrictBaseModel):
    queue: str = Field(min_length=1)


class MetricQueueAssignmentResponse(StrictBaseModel):
    metric_name: str = Field(min_length=1)
    queue: str = Field(min_length=1)


class DeleteMetricQueueResponse(StrictBaseModel):
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
