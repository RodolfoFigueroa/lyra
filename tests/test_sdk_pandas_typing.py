import pandas as pd
from lyra.sdk import Unit
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import TableJobResult
from lyra.sdk.models.plugin import TableColumn, TableOutput
from lyra.sdk.results import normalize_native_result


def test_native_dataframe_accepts_pandas_and_preserves_column_types() -> None:
    frame = pd.DataFrame({"integer": [1], "number": [0.5]}, index=["area"])
    location = GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "area",
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
    output = TableOutput(
        columns=[
            TableColumn(
                name="integer", type="integer", unit=Unit.COUNT, description="Count."
            ),
            TableColumn(
                name="number", type="number", unit=Unit.RATIO, description="Ratio."
            ),
        ]
    )
    result = normalize_native_result(
        "typed_frame", output, frame, job_id="local", location=location
    )
    assert isinstance(result, TableJobResult)
    assert result.index == ["area"]
    assert result.columns == ["integer", "number"]
    assert result.data == [[1, 0.5]]
    assert type(result.data[0][0]) is int
    assert type(result.data[0][1]) is float
