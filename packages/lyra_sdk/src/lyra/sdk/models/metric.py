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


class MetricCatalogResponse(StrictBaseModel):
    """Public metric catalog with a contract-only fingerprint."""

    catalog_fingerprint: str = Field(
        min_length=1,
        description="SHA-256 fingerprint of the public metric catalog contract.",
    )
    metrics: list[MetricInfoV3] = Field(
        description="Client-facing metric metadata sorted by metric name.",
    )


__all__ = ["MetricCatalogResponse", "MetricInfoV3"]
