import asyncio
from typing import Any

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from lyra.sdk.models import DataTypeSchemaInfo

from lyra_app.routes.data_types import list_data_types


def _entry_by_data_type(
    entries: list[DataTypeSchemaInfo],
    data_type: str,
) -> DataTypeSchemaInfo:
    return next(entry for entry in entries if entry.data_type == data_type)


def _feature(
    feature_id: str,
    geometry_type: str = "Polygon",
) -> dict[str, Any]:
    coordinates: Any
    if geometry_type == "MultiPolygon":
        coordinates = [
            [
                [
                    [-99.20, 19.30],
                    [-99.10, 19.30],
                    [-99.10, 19.40],
                    [-99.20, 19.40],
                    [-99.20, 19.30],
                ]
            ]
        ]
    else:
        coordinates = [
            [
                [-99.20, 19.30],
                [-99.10, 19.30],
                [-99.10, 19.40],
                [-99.20, 19.40],
                [-99.20, 19.30],
            ]
        ]

    return {
        "id": feature_id,
        "type": "Feature",
        "geometry": {
            "type": geometry_type,
            "coordinates": coordinates,
        },
        "properties": {},
    }


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": features,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def test_data_types_route_returns_grouped_wrapper_schemas() -> None:
    response = asyncio.run(list_data_types())

    assert set(response.model_dump()) == {"location", "bounds"}
    assert [entry.data_type for entry in response.location] == [
        "cvegeo_list",
        "geojson",
        "met_zone_code",
    ]
    assert [entry.data_type for entry in response.bounds] == [
        "cvegeo_list",
        "geojson",
        "met_zone_code",
    ]
    assert all(entry.wrapper_schema for entry in response.location + response.bounds)


def test_location_geojson_schema_accepts_multiple_features() -> None:
    response = asyncio.run(list_data_types())
    schema = _entry_by_data_type(response.location, "geojson").wrapper_schema

    validate(
        {
            "data_type": "geojson",
            "value": _feature_collection(
                [
                    _feature("area-1"),
                    _feature("area-2", geometry_type="MultiPolygon"),
                ]
            ),
        },
        schema,
    )


def test_bounds_geojson_schema_requires_one_non_multipolygon_feature() -> None:
    response = asyncio.run(list_data_types())
    schema = _entry_by_data_type(response.bounds, "geojson").wrapper_schema

    validate(
        {
            "data_type": "geojson",
            "value": _feature_collection([_feature("area-1")]),
        },
        schema,
    )

    invalid_multiple_features = {
        "data_type": "geojson",
        "value": _feature_collection([_feature("area-1"), _feature("area-2")]),
    }
    invalid_multipolygon = {
        "data_type": "geojson",
        "value": _feature_collection(
            [_feature("area-1", geometry_type="MultiPolygon")]
        ),
    }

    with pytest.raises(ValidationError):
        validate(invalid_multiple_features, schema)
    with pytest.raises(ValidationError):
        validate(invalid_multipolygon, schema)
