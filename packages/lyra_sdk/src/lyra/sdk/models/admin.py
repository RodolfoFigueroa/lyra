"""Models for administrative status and control operations."""

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class InstalledPluginResponse(StrictBaseModel):
    """Configured installed plugin details."""

    distribution: str = Field(min_length=1)
    version: str | None = None
    enabled: bool


class InstalledPluginListResponse(StrictBaseModel):
    """Collection of configured installed plugins."""

    plugins: list[InstalledPluginResponse]


class PluginRoutingResponse(StrictBaseModel):
    """Current metric-to-queue routing configuration."""

    metric_queues: dict[str, str]
    overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    disabled_plugins: list[str] = Field(default_factory=list)
    allowed_queues: list[str] = Field(min_length=1)
    default_queue: str = Field(min_length=1)
