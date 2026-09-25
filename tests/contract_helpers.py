"""Canonical manifest fixtures shared by application integration tests."""

from typing import Any

from lyra.sdk import MetricParameters
from lyra.sdk.models.plugin import MetricManifest
from lyra.sdk.parameters import build_request_schema
from pydantic import Field


class ValueParameters(MetricParameters):
    value: int = Field(description="Example input value.")


class FilterParameters(MetricParameters):
    sector_filters: list[str] = Field(max_length=5, description="Sector filters.")


class YearParameters(MetricParameters):
    year: int = Field(ge=2020, le=2026, description="Reference year.")


class PositiveParameters(MetricParameters):
    value: float = Field(ge=0, description="Nonnegative value.")


def metric_manifest(
    *,
    name: str = "light_metric",
    description: str = "A metric.",
    parameters: type[MetricParameters] | None = ValueParameters,
    spatial: set[str] | None = None,
    output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spatial = spatial or {"location"}
    return MetricManifest.model_validate(
        {
            "name": name,
            "description": description,
            "request_schema": build_request_schema(name, parameters, spatial),
            "spatial_inputs": {key: key for key in sorted(spatial)},
            "output": output
            or {
                "kind": "table",
                "columns": [
                    {
                        "name": "value",
                        "type": "integer",
                        "unit": "count",
                        "description": "Example output value.",
                    }
                ],
            },
        }
    ).model_dump(mode="json")


def plugin_manifest(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 5,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "metrics": [metric],
    }
