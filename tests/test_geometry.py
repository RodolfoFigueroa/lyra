from __future__ import annotations

from typing import Any

import pytest
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import calculate_feature_areas_m2


def _feature_collection(
    geometry: dict[str, Any],
    *,
    crs: str = "EPSG:6372",
) -> GeoJSON:
    return GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "area-1",
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {},
                }
            ],
            "crs": {"type": "name", "properties": {"name": crs}},
        }
    )


def test_calculate_feature_areas_m2_uses_epsg_6372_and_polygon_holes() -> None:
    location = _feature_collection(
        {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]],
            ],
        }
    )

    assert calculate_feature_areas_m2(location) == {"area-1": 84.0}


def test_calculate_feature_areas_m2_supports_multipolygons_and_source_crs() -> None:
    multipolygon = _feature_collection(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                [[[3, 0], [5, 0], [5, 2], [3, 2], [3, 0]]],
            ],
        }
    )
    geographic = _feature_collection(
        {
            "type": "Polygon",
            "coordinates": [
                [
                    [-99.2, 19.3],
                    [-99.1, 19.3],
                    [-99.1, 19.4],
                    [-99.2, 19.4],
                    [-99.2, 19.3],
                ]
            ],
        },
        crs="EPSG:4326",
    )

    assert calculate_feature_areas_m2(multipolygon) == {"area-1": 8.0}
    assert calculate_feature_areas_m2(geographic)["area-1"] > 0


def test_calculate_feature_areas_m2_rejects_unknown_source_crs() -> None:
    location = _feature_collection(
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
        },
        crs="not-a-crs",
    )

    with pytest.raises(ValueError, match="could not be interpreted"):
        calculate_feature_areas_m2(location)


@pytest.mark.parametrize(
    ("geometry", "match"),
    [
        (
            {"type": "Point", "coordinates": [-99.1, 19.4]},
            "require polygon geometry",
        ),
        (
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]],
            },
            "require valid polygon geometry",
        ),
    ],
)
def test_calculate_feature_areas_m2_rejects_non_area_geometry(
    geometry: dict[str, Any],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        calculate_feature_areas_m2(_feature_collection(geometry))
