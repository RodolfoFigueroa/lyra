import pandas as pd
from lyra.sdk.models import TableJobResult


def test_table_result_constructor_accepts_pandas_dataframe() -> None:
    dataframe = pd.DataFrame({"value": [1, 2]}, index=["first", "second"])

    dataframe_result = TableJobResult.from_dataframe("dataframe-job", dataframe)

    assert dataframe_result.index == ["first", "second"]
    assert dataframe_result.columns == ["value"]
    assert dataframe_result.data == [[1], [2]]


def test_table_result_constructor_accepts_pandas_series() -> None:
    series = pd.Series(
        [1, 2],
        index=["first", "second"],
        name=("group", "value"),
    )

    series_result = TableJobResult.from_series("series-job", series)

    assert series_result.index == ["first", "second"]
    assert series_result.columns == ["('group', 'value')"]
    assert series_result.data == [[1], [2]]
