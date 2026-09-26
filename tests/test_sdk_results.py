from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from lyra.sdk import (
    FileOutput,
    FractionOfLocationArea,
    LocationInput,
    MetricResultError,
    PluginDefinition,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)
from lyra.sdk.models.job import TableJobResult

if TYPE_CHECKING:
    from lyra.sdk.models.geometry import GeoJSON


@pytest.fixture
def location() -> GeoJSON:
    return LocationInput.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "a",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                    },
                    "properties": {},
                }
            ],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }
    )


def definition(output: TableOutput | FileOutput) -> PluginDefinition:
    @metric(name="test", description="Test", output=output)
    def handler(location: LocationInput) -> object:
        return location

    return PluginDefinition(metrics=[handler])


def column(name: str = "value", *, nullable: bool = False) -> TableColumn:
    return TableColumn(
        name=name,
        type="integer",
        description="Value",
        unit=Unit.COUNT,
        nullable=nullable,
    )


@pytest.mark.parametrize(
    "value",
    [True, 1.0, float("inf"), float("nan"), None, pd.NA, pd.NaT, {"nested": 1}, [1]],
)
def test_invalid_integer_cells(location: GeoJSON, value: object) -> None:
    plugin = definition(TableOutput(columns=[column()]))
    with pytest.raises(MetricResultError, match=r"test.*row 0, column 'value'"):
        plugin.normalize_result(
            "test",
            pd.DataFrame({"value": [value]}, index=["a"], dtype=object),
            job_id="j",
            location=location,
        )


@pytest.mark.parametrize(
    ("index", "columns"),
    [
        ([1], ["value"]),
        (["b"], ["value"]),
        (["a", "a"], ["value"]),
        (["a"], [1]),
        (["a"], ["extra"]),
        (["a"], ["value", "value"]),
    ],
)
def test_invalid_axes(
    location: GeoJSON, index: list[object], columns: list[object]
) -> None:
    plugin = definition(TableOutput(columns=[column()]))
    frame = pd.DataFrame(1, index=index, columns=columns)
    with pytest.raises(MetricResultError, match=r"index|columns"):
        plugin.normalize_result("test", frame, job_id="j", location=location)


def test_mixed_columns_preserve_types(location: GeoJSON) -> None:
    output = TableOutput(
        columns=[
            column(),
            TableColumn(
                name="fraction", type="number", description="Fraction", unit=Unit.RATIO
            ),
        ]
    )
    plugin = definition(output)
    frame = pd.DataFrame({"value": [2], "fraction": [0.5]}, index=["a"])
    result = plugin.normalize_result("test", frame, job_id="j", location=location)
    assert isinstance(result, TableJobResult)
    assert result.data == [[2, 0.5]]
    assert type(result.data[0][0]) is int


@pytest.mark.parametrize("value", [None, float("nan"), np.int64(3)])
def test_nullable_and_numpy_values(location: GeoJSON, value: object) -> None:
    plugin = definition(TableOutput(columns=[column(nullable=True)]))
    frame = pd.DataFrame({"value": [value]}, index=["a"], dtype=object)
    result = plugin.normalize_result("test", frame, job_id="j", location=location)
    assert isinstance(result, TableJobResult)
    assert result.data == [[3 if isinstance(value, np.integer) else None]]


@pytest.mark.parametrize(
    "value",
    [
        {"value": 1},
        pd.Series([1]),
        TableJobResult(job_id="j", index=["a"], columns=["value"], data=[[1]]),
    ],
)
def test_rejects_old_result_alternatives(location: GeoJSON, value: object) -> None:
    with pytest.raises(MetricResultError, match="DataFrame"):
        definition(TableOutput(columns=[column()])).normalize_result(
            "test", value, job_id="j", location=location
        )


def area_plugin() -> PluginDefinition:
    return definition(
        TableOutput(
            columns=[
                TableColumn(
                    name="area",
                    type="number",
                    unit=Unit.SQUARE_METRE,
                    description="Area",
                    nullable=True,
                    derivations=[
                        FractionOfLocationArea(
                            name="fraction", description="Area fraction"
                        )
                    ],
                )
            ]
        )
    )


@pytest.mark.parametrize(
    ("source", "expected"), [(50, 0.5), (-1e-8, 0.0), (100.00000001, 1.0), (None, None)]
)
def test_area_fraction(
    location: GeoJSON, source: float | None, expected: float | None
) -> None:
    result = area_plugin().normalize_result(
        "test",
        pd.DataFrame({"area": [source]}, index=["a"]),
        job_id="j",
        location=location,
        location_areas_m2={"a": 100},
    )
    assert isinstance(result, TableJobResult)
    assert result.columns == ["area", "fraction"]
    assert result.data[0][1] == expected


@pytest.mark.parametrize(
    "areas",
    [None, {}, {"b": 100}, {"a": 0}, {"a": -1}, {"a": float("inf")}, {"a": True}],
)
def test_invalid_areas(location: GeoJSON, areas: dict[str, float] | None) -> None:
    with pytest.raises(MetricResultError, match="location_areas_m2"):
        area_plugin().normalize_result(
            "test",
            pd.DataFrame({"area": [50]}, index=["a"]),
            job_id="j",
            location=location,
            location_areas_m2=areas,
        )


@pytest.mark.parametrize("source", [-1, 101])
def test_invalid_fractions(location: GeoJSON, source: float) -> None:
    with pytest.raises(MetricResultError, match="fraction"):
        area_plugin().normalize_result(
            "test",
            pd.DataFrame({"area": [source]}, index=["a"]),
            job_id="j",
            location=location,
            location_areas_m2={"a": 100},
        )


def test_file_boundary_and_suffix(tmp_path: Path) -> None:
    plugin = definition(FileOutput(media_type="text/plain", extensions=[".txt"]))
    directory = tmp_path / "job"
    directory.mkdir()
    valid = directory / "RESULT.TXT"
    valid.write_text("ok")
    assert (
        plugin.normalize_result(
            "test", Path("RESULT.TXT"), job_id="j", temp_dir=directory
        ).kind
        == "file"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    link = directory / "escape.txt"
    link.symlink_to(outside)
    wrong = directory / "wrong.csv"
    wrong.write_text("wrong")
    for value in [
        outside,
        link,
        wrong,
        directory,
        directory / "missing.txt",
        str(valid),
    ]:
        with pytest.raises(MetricResultError):
            plugin.normalize_result("test", value, job_id="j", temp_dir=directory)
