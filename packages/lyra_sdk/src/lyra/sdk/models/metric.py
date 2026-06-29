from typing import Any

from lyra.sdk.models.strict import StrictBaseModel
from pydantic import Field


class MetricInfoV2(StrictBaseModel):
    """Catalog metadata for one metric exposed by the API."""

    name: str = Field(description="Public metric name.")
    description: str = Field(description="Human-readable metric description.")
    request_schema: dict[str, Any] = Field(
        description="JSON Schema for the client request payload.",
    )
    result_schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON Schema for successful JSON results.",
    )


__all__ = ["MetricInfoV2"]
