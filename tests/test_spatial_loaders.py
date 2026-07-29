from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

from lyra_app.converters.location import load_from_cvegeos
from lyra_app.loaders.db import get_table_name_for_cvegeos


@pytest.mark.parametrize(
    ("cvegeos", "expected"),
    [
        (["01"], "census_2020_ent"),
        (["01001"], "census_2020_mun"),
        (["010010001"], "census_2020_loc"),
        (["0100100010001"], "census_2020_ageb"),
        (["0100100010001001"], "census_2020_mza"),
    ],
)
def test_cvegeo_table_resolution_accepts_every_supported_level(
    cvegeos: list[str],
    expected: str,
) -> None:
    assert get_table_name_for_cvegeos(cvegeos) == expected


@pytest.mark.parametrize(
    ("cvegeos", "match"),
    [
        ([], "at least one"),
        (["01", "01001"], "same geographic level"),
        (["0"], "unsupported cvegeo length"),
        ([""], "non-empty strings"),
    ],
)
def test_cvegeo_table_resolution_rejects_invalid_collections(
    cvegeos: list[str],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        get_table_name_for_cvegeos(cvegeos)


@pytest.mark.parametrize(
    ("cvegeos", "schema"),
    [
        ([], "public"),
        (["01", "01001"], "public"),
        (["0"], "public"),
        (["01001"], ""),
        (["01001"], "bad-schema"),
        (["01001"], "x" * 64),
    ],
)
def test_location_converter_rejects_invalid_inputs_before_connecting(
    cvegeos: list[str],
    schema: str,
) -> None:
    engine = MagicMock(spec=Engine)

    with pytest.raises(
        ValueError,
        match=r"cvegeo|geographic level|database schema|identifier|63|ASCII",
    ):
        load_from_cvegeos(cvegeos, engine=engine, schema=schema)

    engine.connect.assert_not_called()
