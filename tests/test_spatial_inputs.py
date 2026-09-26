import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import geopandas
import pytest
from lyra.sdk.models.plugin import SpatialInputKind
from shapely.geometry import Point, Polygon
from sqlalchemy import Connection, create_engine

from lyra_app import spatial_inputs
from lyra_app.converters import bounds, build_converter_map, location
from lyra_app.db.connection import ApplicationDatabaseRuntime
from tests.config_helpers import load_test_config


@pytest.mark.parametrize("kind", ["location", "bounds"])
@pytest.mark.parametrize("source", ["geojson", "cvegeo_list", "met_zone_code"])
def test_spatial_resolution_uses_bound_converters_and_executor(
    kind: SpatialInputKind,
    source: Literal["geojson", "cvegeo_list", "met_zone_code"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = geopandas.GeoDataFrame(
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])],
        index=["09002"],
        crs="EPSG:6372",
    )
    geojson = json.loads(geometry.to_json())
    value = {"geojson": geojson, "cvegeo_list": ["09002"], "met_zone_code": "09.01"}[
        source
    ]
    payload = {kind: {"data_type": source, "value": value}, "other": 3}
    original_payload = deepcopy(payload)
    lookups: list[str | list[str]] = []

    def load(reference: str | list[str], *, conn: Connection) -> geopandas.GeoDataFrame:
        assert not conn.closed
        lookups.append(reference)
        return geometry

    module = location if kind == "location" else bounds
    prefix = "load_geometries" if kind == "location" else "load_bounds"
    monkeypatch.setattr(module, f"{prefix}_from_cvegeos", load)
    monkeypatch.setattr(module, f"{prefix}_from_met_zone_code", load)
    runtime = ApplicationDatabaseRuntime(load_test_config(tmp_path))

    async def exercise() -> spatial_inputs.SpatialInputResolution:
        await runtime.start()
        try:
            runtime.require_spatial_engine().dispose()
            runtime.spatial_engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}
            )
            if source == "geojson":
                monkeypatch.setattr(
                    runtime.spatial_engine,
                    "connect",
                    Mock(
                        side_effect=AssertionError(
                            "GeoJSON must not query the database"
                        )
                    ),
                )
            return await runtime.run_spatial(
                spatial_inputs.resolve_spatial_inputs_with_metadata,
                payload,
                {kind: kind},
                converter_map=build_converter_map(runtime.require_spatial_engine()),
            )
        finally:
            await runtime.close()

    resolution = asyncio.run(exercise())
    assert resolution.input == {kind: geojson, "other": 3}
    assert payload == original_payload
    assert lookups == ([] if source == "geojson" else [value])
    if kind == "bounds":
        assert resolution.row_identity is None
    else:
        assert resolution.row_identity is not None
        expected = {
            "geojson": {"field": "id"},
            "cvegeo_list": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:municipality",
                "version": "2020",
            },
            "met_zone_code": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        }
        assert resolution.row_identity.model_dump(exclude_none=True) == expected[source]


@pytest.mark.parametrize("kind", ["location", "bounds"])
@pytest.mark.parametrize("source", ["geojson", "cvegeo_list", "met_zone_code"])
def test_spatial_resolution_rejects_points(
    kind: SpatialInputKind,
    source: Literal["geojson", "cvegeo_list", "met_zone_code"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = geopandas.GeoDataFrame(
        geometry=[Point(1, 2)], index=["09002"], crs="EPSG:6372"
    )
    values = {
        "geojson": json.loads(geometry.to_json()),
        "cvegeo_list": ["09002"],
        "met_zone_code": "09.01",
    }
    module = location if kind == "location" else bounds
    prefix = "load_geometries" if kind == "location" else "load_bounds"
    lookup = Mock(return_value=geometry)
    monkeypatch.setattr(module, f"{prefix}_from_cvegeos", lookup)
    monkeypatch.setattr(module, f"{prefix}_from_met_zone_code", lookup)
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(spatial_inputs.SpatialInputValidationError) as exc:
            spatial_inputs.resolve_spatial_inputs(
                {kind: {"data_type": source, "value": values[source]}},
                {kind: kind},
                converter_map=build_converter_map(engine),
            )
        assert all(error["loc"][0] == kind for error in exc.value.errors)
        assert lookup.call_count == (0 if source == "geojson" else 1)
    finally:
        engine.dispose()
