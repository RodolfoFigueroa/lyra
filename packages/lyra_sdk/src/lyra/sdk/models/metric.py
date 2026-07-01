from typing import Any

from lyra.sdk.models.plugin_v3 import OutputSpecV3
from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class MetricInfoV3(StrictBaseModel):
    """Catalog metadata for one schema v3 metric exposed by the API."""

    name: str = Field(description="Public metric name.")
    description: str = Field(description="Human-readable metric description.")
    request_schema: dict[str, Any] = Field(
        description="Effective JSON Schema for the client request payload.",
    )
    output: OutputSpecV3 = Field(
        description="Successful metric output declaration.",
    )


__all__ = ["MetricInfoV3"]
