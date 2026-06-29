from __future__ import annotations

from copy import deepcopy
from typing import Any

import lyra.sdk.models as sdk_models
import pytest
from lyra.sdk.models import PluginManifestV3
from lyra.sdk.models.plugin_v3 import BatchInputV3, FileOutputV3, TableOutputV3
from pydantic import ValidationError


def _static_metric(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "name": "urbanized_area",
        "description": "Compute urbanized area statistics.",
        "queue": "interactive",
        "entrypoint": "urban_metrics.runner:run",
        "inputs": {
            "location": {"kind": "location"},
            "year": {
                "kind": "integer",
                "minimum": 2020,
                "maximum": 2026,
            },
        },
        "output": {
            "kind": "table",
            "columns": [
                {
                    "name": "area_m2",
                    "type": "number",
                    "unit": "m2",
                    "description": "Urbanized area in square meters.",
                }
            ],
        },
    }
    if overrides:
        metric.update(overrides)
    return metric


def _manifest(
    metric_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "plugin": {"name": "urban-metrics", "version": "0.1.0"},
        "metrics": [_static_metric(metric_overrides)],
    }


def _dynamic_metric() -> dict[str, Any]:
    return _static_metric(
        {
            "name": "accessibility_by_destination",
            "description": "Compute accessibility by destination.",
            "inputs": {
                "location": {"kind": "location"},
                "destination_categories": {
                    "kind": "batch",
                    "max_items": 12,
                    "value": {
                        "kind": "string",
                        "min_length": 1,
                        "max_length": 128,
                    },
                    "label": True,
                },
                "travel_minutes": {
                    "kind": "integer",
                    "minimum": 1,
                    "maximum": 180,
                },
            },
            "output": {
                "kind": "table",
                "batched_columns": [
                    {
                        "source": "destination_categories",
                        "name": "accessibility_{key}",
                        "type": "number",
                        "unit": "destinations",
                        "description": "Accessible destinations for {label}.",
                    }
                ],
            },
        }
    )


def _assert_invalid(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        PluginManifestV3.model_validate(raw)


def test_sdk_public_surface_exposes_v3_manifest_entrypoints() -> None:
    assert sdk_models.PluginManifestV3 is PluginManifestV3
    assert hasattr(sdk_models, "compile_plugin_manifest")
    assert not hasattr(sdk_models, "PluginManifestV2")
    assert not hasattr(sdk_models, "MetricInfoV2")


def test_manifest_v3_accepts_minimal_static_table_metric() -> None:
    manifest = PluginManifestV3.model_validate(_manifest())
    metric = manifest.metrics[0]

    assert manifest.schema_version == 3
    assert metric.queue == "interactive"
    assert isinstance(metric.output, TableOutputV3)
    assert metric.output.columns[0].nullable is False


def test_manifest_v3_accepts_dynamic_table_metric() -> None:
    manifest = PluginManifestV3.model_validate(
        {
            "schema_version": 3,
            "plugin": {"name": "accessibility-metrics", "version": "0.1.0"},
            "metrics": [_dynamic_metric()],
        }
    )
    metric = manifest.metrics[0]
    batch = metric.inputs["destination_categories"]

    assert isinstance(batch, BatchInputV3)
    assert batch.max_items == 12
    assert isinstance(metric.output, TableOutputV3)
    assert metric.output.batched_columns[0].name == "accessibility_{key}"


def test_manifest_v3_accepts_file_metric_with_bounds() -> None:
    manifest = PluginManifestV3.model_validate(
        _manifest(
            {
                "name": "land_cover_raster",
                "description": "Generate a land cover raster.",
                "inputs": {
                    "bounds": {"kind": "bounds"},
                    "year": {"kind": "integer", "minimum": 2020, "maximum": 2026},
                },
                "output": {
                    "kind": "file",
                    "media_type": "image/tiff",
                    "extensions": [".tif", ".tiff"],
                },
            }
        )
    )

    assert isinstance(manifest.metrics[0].output, FileOutputV3)


def test_manifest_v3_accepts_plugin_owned_json_schema_input() -> None:
    manifest = PluginManifestV3.model_validate(
        _manifest(
            {
                "inputs": {
                    "location": {"kind": "location"},
                    "advanced_filter": {
                        "kind": "json_schema",
                        "required": False,
                        "schema": {
                            "type": "object",
                            "required": ["field", "op"],
                            "properties": {
                                "field": {"type": "string"},
                                "op": {"enum": ["eq", "gt", "lt"]},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
            }
        )
    )

    assert manifest.metrics[0].inputs["advanced_filter"].required is False


def test_manifest_v3_rejects_unknown_fields() -> None:
    raw = _manifest({"request_schema": {"type": "object"}})

    _assert_invalid(raw, "Extra inputs")


def test_manifest_v3_rejects_invalid_schema_version() -> None:
    raw = _manifest()
    raw["schema_version"] = 2

    _assert_invalid(raw, "schema_version")


def test_manifest_v3_rejects_duplicate_metric_names() -> None:
    raw = _manifest()
    raw["metrics"].append(_static_metric())

    _assert_invalid(raw, "duplicate metric name")


@pytest.mark.parametrize(
    "entrypoint",
    [
        "urban_metrics.runner.run",
        "urban_metrics.runner:run:again",
        "urban-metrics.runner:run",
        "urban_metrics.runner:",
        ":run",
    ],
)
def test_manifest_v3_rejects_invalid_entrypoint_strings(entrypoint: str) -> None:
    raw = _manifest({"entrypoint": entrypoint})

    _assert_invalid(raw, "module:function")


def test_manifest_v3_rejects_metric_without_spatial_input() -> None:
    raw = _manifest(
        {
            "inputs": {"year": {"kind": "integer"}},
            "output": {
                "kind": "file",
                "media_type": "application/json",
                "extensions": [".json"],
            },
        }
    )

    _assert_invalid(raw, "location or bounds")


def test_manifest_v3_rejects_table_metric_without_location_input() -> None:
    raw = _manifest({"inputs": {"bounds": {"kind": "bounds"}}})

    _assert_invalid(raw, "inputs.location")


def test_manifest_v3_rejects_batch_input_without_batched_column() -> None:
    raw = _manifest()
    raw["metrics"][0]["inputs"]["destination_categories"] = {
        "kind": "batch",
        "max_items": 12,
        "value": {"kind": "string"},
    }

    _assert_invalid(raw, "referenced by batched_columns")


def test_manifest_v3_rejects_batched_column_source_missing_from_inputs() -> None:
    raw = _manifest(
        {
            "output": {
                "kind": "table",
                "batched_columns": [
                    {
                        "source": "destination_categories",
                        "name": "accessibility_{key}",
                        "type": "number",
                        "unit": "destinations",
                        "description": "Accessible destinations for {label}.",
                    }
                ],
            }
        }
    )

    _assert_invalid(raw, "not defined in inputs")


def test_manifest_v3_rejects_batched_column_source_that_is_not_batch() -> None:
    raw = _manifest(
        {
            "inputs": {
                "location": {"kind": "location"},
                "destination_categories": {"kind": "string"},
            },
            "output": {
                "kind": "table",
                "batched_columns": [
                    {
                        "source": "destination_categories",
                        "name": "accessibility_{key}",
                        "type": "number",
                        "unit": "destinations",
                        "description": "Accessible destinations for {label}.",
                    }
                ],
            },
        }
    )

    _assert_invalid(raw, "must reference a batch input")


def test_manifest_v3_rejects_unsupported_batched_template_fields() -> None:
    raw = _manifest({"output": deepcopy(_dynamic_metric()["output"])})
    raw["metrics"][0]["inputs"] = deepcopy(_dynamic_metric()["inputs"])
    raw["metrics"][0]["output"]["batched_columns"][0]["name"] = "accessibility_{value}"

    _assert_invalid(raw, "unsupported field")


def test_manifest_v3_rejects_duplicate_static_columns() -> None:
    raw = _manifest()
    raw["metrics"][0]["output"]["columns"].append(
        {
            "name": "area_m2",
            "type": "number",
            "unit": "m2",
            "description": "Duplicate column.",
        }
    )

    _assert_invalid(raw, "duplicate")


def test_manifest_v3_rejects_invalid_file_output_extension() -> None:
    raw = _manifest(
        {
            "output": {
                "kind": "file",
                "media_type": "image/tiff",
                "extensions": ["tif"],
            }
        }
    )

    _assert_invalid(raw, "extension")


def test_manifest_v3_rejects_invalid_raw_json_schema_input() -> None:
    raw = _manifest(
        {
            "inputs": {
                "location": {"kind": "location"},
                "advanced_filter": {
                    "kind": "json_schema",
                    "schema": {"type": "not-a-json-schema-type"},
                },
            }
        }
    )

    _assert_invalid(raw, "invalid json_schema.schema")


def test_manifest_v3_rejects_lyra_owned_input_defaults() -> None:
    raw = _manifest({"inputs": {"location": {"kind": "location", "default": {}}}})

    _assert_invalid(raw, "location inputs must not define default")


@pytest.mark.parametrize(
    ("metric_overrides", "match"),
    [
        (
            {"inputs": {"location": {"kind": "location", "required": False}}},
            "location inputs must be required",
        ),
        (
            {
                "inputs": {"bounds": {"kind": "bounds", "required": False}},
                "output": {
                    "kind": "file",
                    "media_type": "image/tiff",
                    "extensions": [".tif"],
                },
            },
            "bounds inputs must be required",
        ),
    ],
)
def test_manifest_v3_rejects_optional_spatial_inputs(
    metric_overrides: dict[str, Any],
    match: str,
) -> None:
    _assert_invalid(_manifest(metric_overrides), match)


def test_manifest_v3_rejects_optional_batch_inputs() -> None:
    raw = _manifest()
    raw["metrics"][0] = _dynamic_metric()
    raw["metrics"][0]["inputs"]["destination_categories"]["required"] = False

    _assert_invalid(raw, "batch inputs must be required")
