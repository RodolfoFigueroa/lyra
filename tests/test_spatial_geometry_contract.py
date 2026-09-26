from typing import Any
from unittest.mock import create_autospec

import pytest
from jsonschema import Draft202012Validator
from lyra.sdk import (
    BoundsInput,
    LocationInput,
    MetricInputError,
    PluginDefinition,
    RunContext,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)
from lyra.sdk.models.geometry import GeoJSON, PointGeometry, SingleGeoJSON
from lyra.sdk.models.job import JobEnvelope
from pydantic import ValidationError


def _collection(*geometry_types: str) -> dict[str, Any]:
    ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
    coordinates = {"Point": [0, 0], "Polygon": [ring], "MultiPolygon": [[ring]]}
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": str(index),
                "geometry": {"type": kind, "coordinates": coordinates[kind]},
                "properties": {"label": "retained"},
            }
            for index, kind in enumerate(geometry_types)
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:6372"}},
    }


@pytest.mark.parametrize(
    ("model", "types", "valid"),
    [
        (GeoJSON, ("Polygon",), True),
        (GeoJSON, ("MultiPolygon",), True),
        (GeoJSON, ("Polygon", "MultiPolygon"), True),
        (GeoJSON, ("Point",), False),
        (GeoJSON, ("Polygon", "Point"), False),
        (GeoJSON, (), False),
        (SingleGeoJSON, ("Polygon",), True),
        (SingleGeoJSON, ("Point",), False),
        (SingleGeoJSON, ("MultiPolygon",), False),
        (SingleGeoJSON, ("Polygon", "Polygon"), False),
        (SingleGeoJSON, (), False),
    ],
)
def test_spatial_model_and_schema_agree(
    model: type[GeoJSON] | type[SingleGeoJSON], types: tuple[str, ...], *, valid: bool
) -> None:
    payload = _collection(*types)
    assert Draft202012Validator(model.model_json_schema()).is_valid(payload) is valid
    if valid:
        assert model.model_validate(payload).model_dump() == payload
    else:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_standalone_point_geometry_remains_importable() -> None:
    assert PointGeometry(type="Point", coordinates=[0, 0]).type == "Point"


@pytest.mark.parametrize("field", ["location", "bounds"])
def test_sdk_rejects_resolved_points_before_handler(field: str) -> None:
    called = False

    @metric(
        name="polygon_contract",
        description="Check polygon inputs.",
        output=TableOutput(
            columns=[
                TableColumn(
                    name="value", type="integer", unit=Unit.COUNT, description="Count."
                )
            ]
        ),
    )
    def handler(location: LocationInput, bounds: BoundsInput) -> None:
        nonlocal called
        called = bool(location.features and bounds.features)

    plugin = PluginDefinition(metrics=[handler])
    inputs = {"location": _collection("Polygon"), "bounds": _collection("Polygon")}
    inputs[field] = _collection("Point")
    job = JobEnvelope(job_id="point-job", metric="polygon_contract", input=inputs)
    with pytest.raises(MetricInputError, match=field):
        plugin(job, create_autospec(RunContext, instance=True))
    assert called is False
