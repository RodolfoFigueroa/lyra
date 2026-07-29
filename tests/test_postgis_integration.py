import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import geopandas
import pytest
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB
from pandas.errors import DatabaseError
from shapely.geometry import Point, Polygon
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.schema import CreateSchema, DropSchema

from lyra_app.loaders.db import (
    get_met_zone_code_from_name,
    load_bounds_from_cvegeos,
    load_bounds_from_met_zone_code,
    load_geometries_from_cvegeos,
    load_geometries_from_met_zone_code,
)

pytestmark = pytest.mark.integration

_DATABASE_URL_VARIABLE = "LYRA_TEST_POSTGIS_URL"
_INTERSECTING_BOUNDS = Bounds(0, 0, 10, 10)
_NON_INTERSECTING_BOUNDS = Bounds(100, 100, 110, 110)
_FIXTURE_SQL = Path(__file__).parent / "fixtures" / "postgis_integration.sql"


class _CheckedOutPool(Protocol):
    def checkedout(self) -> int: ...


@pytest.fixture(scope="module")
def postgis_database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get(_DATABASE_URL_VARIABLE)
    if database_url is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")

    schema = f"lyra_integration_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    query_engine: Engine | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(CreateSchema(schema))
            connection.execute(text("SET LOCAL search_path TO public"))
            connection.execute(
                text(f'SET LOCAL search_path TO "{schema}", public')
            )  # test fixture identifier is generated, not caller-controlled
            for statement in _FIXTURE_SQL.read_text().split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)

        query_engine = create_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
        )
        yield query_engine, schema
    finally:
        if query_engine is not None:
            query_engine.dispose()
        try:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        finally:
            admin_engine.dispose()


def _assert_crs_6372(frame: geopandas.GeoDataFrame) -> None:
    assert frame.crs is not None
    assert frame.crs.to_epsg() == 6372


def _assert_no_checked_out_connections(engine: Engine) -> None:
    pool = cast("_CheckedOutPool", engine.pool)
    assert pool.checkedout() == 0


def test_load_denue_from_bounds_characterizes_spatial_results(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    database = PostgresLyraDB(postgis_engine, schema=schema)

    result = database.load_denue_from_bounds(
        _INTERSECTING_BOUNDS,
        year=2025,
        month=11,
    )

    assert list(result.columns) == ["per_ocu", "codigo_act", "geometry"]
    assert result["per_ocu"].tolist() == ["0 a 5 personas"]
    assert result["codigo_act"].tolist() == ["111111"]
    assert result.geometry.iloc[0] == Point(1, 1)
    _assert_crs_6372(result)
    assert database.load_denue_from_bounds(
        _NON_INTERSECTING_BOUNDS,
        year=2025,
        month=11,
    ).empty
    _assert_no_checked_out_connections(postgis_engine)


def test_load_mesh_from_bounds_characterizes_spatial_results(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    database = PostgresLyraDB(postgis_engine, schema=schema)

    result = database.load_mesh_from_bounds(_INTERSECTING_BOUNDS, level=9)

    assert list(result.columns) == ["codigo", "geometry"]
    assert result["codigo"].tolist() == ["mesh-inside"]
    assert result.geometry.iloc[0] == Polygon([(2, 2), (4, 2), (4, 4), (2, 4)])
    _assert_crs_6372(result)
    assert database.load_mesh_from_bounds(
        _NON_INTERSECTING_BOUNDS,
        level=9,
    ).empty
    _assert_no_checked_out_connections(postgis_engine)


def test_load_census_from_bounds_characterizes_spatial_results(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    database = PostgresLyraDB(postgis_engine, schema=schema)

    result = database.load_census_from_bounds(
        _INTERSECTING_BOUNDS,
        level="mun",
        columns=["cvegeo", "pobtot"],
    )

    assert list(result.columns) == ["cvegeo", "pobtot", "geometry"]
    assert result["cvegeo"].tolist() == ["01001"]
    assert result["pobtot"].tolist() == [100]
    assert result.geometry.iloc[0] == Polygon([(5, 5), (7, 5), (7, 7), (5, 7)])
    _assert_crs_6372(result)
    assert database.load_census_from_bounds(
        _NON_INTERSECTING_BOUNDS,
        level="mun",
        columns=["cvegeo", "pobtot"],
    ).empty
    _assert_no_checked_out_connections(postgis_engine)


def test_repeated_calls_reuse_the_pooled_engine(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    database = PostgresLyraDB(postgis_engine, schema=schema)

    first = database.load_mesh_from_bounds(_INTERSECTING_BOUNDS)
    second = database.load_mesh_from_bounds(_INTERSECTING_BOUNDS)

    assert first.equals(second)
    assert postgis_engine.pool.status().startswith("Pool size: 1")
    _assert_no_checked_out_connections(postgis_engine)


def test_database_exception_returns_connection_to_pool(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    database = PostgresLyraDB(postgis_engine, schema=schema)

    with pytest.raises(DatabaseError):
        database.load_census_from_bounds(
            _INTERSECTING_BOUNDS,
            level="mun",
            columns=["missing_column"],
        )

    _assert_no_checked_out_connections(postgis_engine)
    assert not database.load_mesh_from_bounds(_INTERSECTING_BOUNDS).empty
    _assert_no_checked_out_connections(postgis_engine)


def test_application_spatial_loaders_are_schema_qualified_and_deterministic(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database
    requested = ["01002", "01001", "01002"]

    with postgis_engine.connect() as connection:
        geometries = load_geometries_from_cvegeos(
            requested,
            conn=connection,
            schema=schema,
        )
        bounds = load_bounds_from_cvegeos(
            requested,
            conn=connection,
            schema=schema,
        )
        metropolitan_geometries = load_geometries_from_met_zone_code(
            "01",
            conn=connection,
            schema=schema,
        )
        metropolitan_bounds = load_bounds_from_met_zone_code(
            "01",
            conn=connection,
            schema=schema,
        )
        match = get_met_zone_code_from_name(
            "Aguascaliente",
            conn=connection,
            schema=schema,
        )

    assert geometries.index.tolist() == requested
    assert geometries.columns.tolist() == ["geometry"]
    _assert_crs_6372(geometries)
    assert bounds.columns.tolist() == ["geometry"]
    _assert_crs_6372(bounds)
    assert metropolitan_geometries.index.tolist() == [
        "0100100010001",
        "0100100010002",
    ]
    _assert_crs_6372(metropolitan_geometries)
    assert metropolitan_bounds.columns.tolist() == ["geometry"]
    _assert_crs_6372(metropolitan_bounds)
    assert match == ("01", "Aguascalientes")
    _assert_no_checked_out_connections(postgis_engine)
