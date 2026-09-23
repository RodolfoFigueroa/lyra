"""Models for administrative status and control operations."""

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class PluginRepoResponse(StrictBaseModel):
    """Configured plugin repository details."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    ref: str | None = None
    resolved_ref: str | None = None
    enabled: bool


class PluginRepoListResponse(StrictBaseModel):
    """Collection of configured plugin repositories."""

    repos: list[PluginRepoResponse]


class PluginRoutingResponse(StrictBaseModel):
    """Current metric-to-queue routing configuration."""

    metric_queues: dict[str, str]
    overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    disabled_repos: list[str] = Field(default_factory=list)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
