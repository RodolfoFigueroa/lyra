from __future__ import annotations

from typing import Any

import pytest
from jsonschema.validators import validator_for
from lyra.sdk.models import PluginManifestV3, compile_plugin_manifest


def _static_metric(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "name": "urbanized_area",
        "description": "Compute urbanized area statistics.",
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


def _compile(raw: dict[str, Any]) -> dict[str, Any]:
    manifest = PluginManifestV3.model_validate(raw)
    return compile_plugin_manifest(manifest).model_dump(mode="json")


def _assert_valid_json_schema(schema: dict[str, Any]) -> None:
    validator_for(schema).check_schema(schema)


def _assert_valid_payload(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    validator_cls = validator_for(schema)
    validator_cls(schema).validate(payload)


def test_compile_v3_static_table_metric_request_schema() -> None:
    compiled = _compile(_manifest())
    metric = compiled["metrics"][0]
    schema = metric["request_schema"]

    assert "queue" not in metric
    assert metric["spatial_inputs"] == {"location": "location"}
    assert metric["batch_inputs"] == []
    assert schema["type"] == "object"
    assert schema["required"] == ["location", "year"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["year"] == {
        "type": "integer",
        "minimum": 2020,
        "maximum": 2026,
    }
    assert "oneOf" in schema["properties"]["location"]
    assert "GeoJSONLocationWrapperV3" in schema["$defs"]
    assert metric["output"] == {
        "kind": "table",
        "columns": [
            {
                "name": "area_m2",
                "type": "number",
                "unit": "m2",
                "description": "Urbanized area in square meters.",
                "nullable": False,
            }
        ],
        "batched_columns": [],
    }
    _assert_valid_json_schema(schema)
    _assert_valid_payload(
        schema,
        {
            "location": {"data_type": "cvegeo_list", "value": ["09002"]},
            "year": 2025,
        },
    )


def test_compile_v3_optional_nullable_scalar_input() -> None:
    compiled = _compile(
        _manifest(
            {
                "inputs": {
                    "location": {"kind": "location"},
                    "threshold": {
                        "kind": "number",
                        "minimum": 0,
                        "required": False,
                        "nullable": True,
                        "default": None,
                        "examples": [None, 1.5],
                    },
                },
            }
        )
    )
    schema = compiled["metrics"][0]["request_schema"]
    threshold_schema = schema["properties"]["threshold"]

    assert schema["required"] == ["location"]
    assert threshold_schema == {
        "anyOf": [{"type": "number", "minimum": 0.0}, {"type": "null"}],
        "default": None,
        "examples": [None, 1.5],
    }
    _assert_valid_json_schema(schema)


def test_compile_v3_rejects_invalid_default_against_compiled_schema() -> None:
    manifest = PluginManifestV3.model_validate(
        _manifest(
            {
                "inputs": {
                    "location": {"kind": "location"},
                    "year": {
                        "kind": "integer",
                        "minimum": 2020,
                        "maximum": 2026,
                        "default": 2019,
                    },
                }
            }
        )
    )

    with pytest.raises(ValueError, match=r"metrics\[0\].inputs.year.default"):
        compile_plugin_manifest(manifest)


def test_compile_v3_dynamic_table_metric_batch_schema() -> None:
    compiled = _compile(
        {
            "schema_version": 3,
            "plugin": {"name": "accessibility-metrics", "version": "0.1.0"},
            "metrics": [_dynamic_metric()],
        }
    )
    metric = compiled["metrics"][0]
    batch_schema = metric["request_schema"]["properties"]["destination_categories"]

    assert metric["batch_inputs"] == ["destination_categories"]
    assert metric["request_schema"]["required"] == [
        "location",
        "destination_categories",
        "travel_minutes",
    ]
    assert batch_schema == {
        "type": "array",
        "minItems": 1,
        "maxItems": 12,
        "uniqueItems": True,
        "items": {
            "type": "object",
            "required": ["key", "value"],
            "additionalProperties": False,
            "properties": {
                "key": {
                    "type": "string",
                    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
                    "minLength": 1,
                    "maxLength": 64,
                },
                "value": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                },
                "label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                },
            },
        },
    }
    assert metric["output"] == {
        "kind": "table",
        "columns": [],
        "batched_columns": [
            {
                "source": "destination_categories",
                "name": "accessibility_{key}",
                "type": "number",
                "unit": "destinations",
                "description": "Accessible destinations for {label}.",
                "nullable": False,
            }
        ],
    }
    _assert_valid_json_schema(metric["request_schema"])


def test_compile_v3_mixed_static_and_dynamic_table_output() -> None:
    metric = _dynamic_metric()
    metric["output"]["columns"] = [
        {
            "name": "total_destinations",
            "type": "integer",
            "unit": "destinations",
            "description": "Total destinations across all categories.",
        }
    ]
    compiled = _compile(
        {
            "schema_version": 3,
            "plugin": {"name": "accessibility-metrics", "version": "0.1.0"},
            "metrics": [metric],
        }
    )
    output = compiled["metrics"][0]["output"]

    assert output == {
        "kind": "table",
        "columns": [
            {
                "name": "total_destinations",
                "type": "integer",
                "unit": "destinations",
                "description": "Total destinations across all categories.",
                "nullable": False,
            }
        ],
        "batched_columns": [
            {
                "source": "destination_categories",
                "name": "accessibility_{key}",
                "type": "number",
                "unit": "destinations",
                "description": "Accessible destinations for {label}.",
                "nullable": False,
            }
        ],
    }


def test_compile_v3_file_metric_with_bounds_spatial_schema() -> None:
    compiled = _compile(
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
    metric = compiled["metrics"][0]
    schema = metric["request_schema"]

    assert metric["spatial_inputs"] == {"bounds": "bounds"}
    assert metric["batch_inputs"] == []
    assert schema["required"] == ["bounds", "year"]
    assert "GeoJSONBoundsWrapperV3" in schema["$defs"]
    assert metric["output"] == {
        "kind": "file",
        "media_type": "image/tiff",
        "extensions": [".tif", ".tiff"],
    }
    _assert_valid_json_schema(schema)


def test_compile_v3_json_schema_escape_hatch_copies_schema() -> None:
    compiled = _compile(
        _manifest(
            {
                "inputs": {
                    "location": {"kind": "location"},
                    "advanced_filter": {
                        "kind": "json_schema",
                        "required": False,
                        "description": "Optional advanced filter.",
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
    advanced_filter_schema = compiled["metrics"][0]["request_schema"]["properties"][
        "advanced_filter"
    ]

    assert advanced_filter_schema == {
        "type": "object",
        "required": ["field", "op"],
        "properties": {
            "field": {"type": "string"},
            "op": {"enum": ["eq", "gt", "lt"]},
        },
        "additionalProperties": False,
        "description": "Optional advanced filter.",
    }


def test_compile_v3_is_deterministic() -> None:
    manifest = PluginManifestV3.model_validate(_manifest())

    first = compile_plugin_manifest(manifest).model_dump(mode="json")
    second = compile_plugin_manifest(manifest).model_dump(mode="json")

    assert first == second
