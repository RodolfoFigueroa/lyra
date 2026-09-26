"""Offline checks of the Earth Engine evaluation workflow and adapter lifecycle."""

import importlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import TableJobResult
from lyra.sdk.plugin_cli import build_manifest, check_manifest
from lyra.utils.geometry import convert_geojson_to_gdf

from tests.smoke_plugin_helpers import feature_collection

SCENARIOS = Path(__file__).parent / "fixtures/skill_workflows/scenarios"
ADAPTER = """import pandas as pd
from lyra.sdk import (
    LocationInput, PluginDefinition, TableColumn, TableOutput, Unit, metric,
)
from lyra.utils.geometry import convert_geojson_to_gdf
from ee_trial_workflow import calculate_metric

@metric(
    name="mean_elevation",
    description="Mean SRTM elevation at 30 metre reduction resolution.",
    output=TableOutput(columns=[TableColumn(
        name="mean_elevation", type="number", unit=Unit.METRE, nullable=True,
        description="Mean elevation; null when no source pixels are available."
    )]),
)
def run(location: LocationInput) -> pd.DataFrame:
    return calculate_metric(convert_geojson_to_gdf(location))

def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[run])
"""


@pytest.fixture
def adapted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ModuleType, ModuleType, Mock]]:
    shutil.copyfile(SCENARIOS / "earth_engine.py", tmp_path / "ee_trial_workflow.py")
    (tmp_path / "ee_trial_adapter.py").write_text(ADAPTER, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "ee-trial"\nversion = "0.1.0"\n'
        '[tool.lyra]\nfactory = "ee_trial_adapter:create_plugin"\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("ee_trial_workflow", "ee_trial_adapter"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    engine = Mock()
    monkeypatch.setitem(sys.modules, "ee", engine)
    try:
        workflow = importlib.import_module("ee_trial_workflow")
        adapter = importlib.import_module("ee_trial_adapter")
        yield workflow, adapter, engine
    finally:
        for name in ("ee_trial_workflow", "ee_trial_adapter"):
            sys.modules.pop(name, None)


def test_import_factory_and_manifest_do_not_use_earth_engine(
    adapted: tuple[ModuleType, ModuleType, Mock], tmp_path: Path
) -> None:
    _, adapter, engine = adapted
    assert adapter.create_plugin().metric_names == ("mean_elevation",)
    build_manifest(tmp_path)
    assert check_manifest(tmp_path) == (True, "")
    assert engine.mock_calls == []


def test_earth_engine_adapter_preserves_reprojection_and_missing_values(
    adapted: tuple[ModuleType, ModuleType, Mock],
) -> None:
    workflow, adapter, engine = adapted
    geographic = convert_geojson_to_gdf(
        GeoJSON.model_validate(feature_collection(("zone-z", "zone-a")))
    )
    projected = geographic.to_crs("EPSG:3857")
    # No workflow-specific feature properties are supplied.
    location = GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
            "features": list(projected.iterfeatures()),
        }
    )
    image = engine.Image.return_value.select.return_value
    image.reduceRegion.return_value.get.return_value.getInfo.side_effect = [
        124.5,
        None,
        124.5,
        None,
    ]
    actual = adapter.run(location=location)
    expected = workflow.calculate_metric(projected)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.index.tolist() == ["zone-z", "zone-a"]
    assert actual.iloc[0, 0] == pytest.approx(124.5)
    assert pd.isna(actual.iloc[1, 0])
    engine.Image.assert_called_with("USGS/SRTMGL1_003")
    engine.Image.return_value.select.assert_called_with("elevation")
    expected_geometry = gpd.GeoDataFrame.from_features(
        [
            {"type": "Feature", "geometry": call.args[0], "properties": {}}
            for call in engine.Geometry.call_args_list[:2]
        ],
        crs="EPSG:4326",
    )
    assert expected_geometry.geometry.geom_equals_exact(
        gpd.GeoSeries(geographic.geometry.to_numpy(), crs=geographic.crs),
        tolerance=1e-8,
    ).all()
    for call in image.reduceRegion.call_args_list:
        assert call.kwargs["scale"] == 30
        assert call.kwargs["reducer"] is engine.Reducer.mean.return_value
    engine.Authenticate.assert_not_called()
    engine.Initialize.assert_not_called()
    plugin = adapter.create_plugin()
    normalized = plugin.normalize_result(
        "mean_elevation", actual, job_id="offline", location=location
    )
    assert isinstance(normalized, TableJobResult)
    assert normalized.index == ["zone-z", "zone-a"]
    assert normalized.data == [[124.5], [None]]
