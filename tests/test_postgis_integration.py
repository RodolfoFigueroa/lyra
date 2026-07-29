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

pytestmark = pytest.mark.integration

_DATABASE_URL_VARIABLE = "LYRA_TEST_POSTGIS_URL"
_INTERSECTING_BOUNDS = Bounds(0, 0, 10, 10)
_NON_INTERSECTING_BOUNDS = Bounds(100, 100, 110, 110)
_FIXTURE_SQL = Path(__file__).parent / "fixtures" / "postgis_integration.sql"


class _CheckedOutPool(Protocol):
    def checkedout(self) -> int: ...


@pytest.fixture(scope="module")
def postgis_engine() -> Iterator[Engine]:
    database_url = os.environ.get(_DATABASE_URL_VARIABLE)
    if database_url is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")

    schema = f"lyra_integration_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    query_engine: Engine | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            for statement in _FIXTURE_SQL.read_text().split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)

        query_engine = create_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        yield query_engine
    finally:
        if query_engine is not None:
            query_engine.dispose()
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            admin_engine.dispose()


def _assert_crs_6372(frame: geopandas.GeoDataFrame) -> None:
    assert frame.crs is not None
    assert frame.crs.to_epsg() == 6372


def _assert_no_checked_out_connections(engine: Engine) -> None:
    pool = cast("_CheckedOutPool", engine.pool)
    assert pool.checkedout() == 0


def test_load_denue_from_bounds_characterizes_spatial_results(
    postgis_engine: Engine,
) -> None:
    database = PostgresLyraDB(postgis_engine)

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
    postgis_engine: Engine,
) -> None:
    database = PostgresLyraDB(postgis_engine)

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
    postgis_engine: Engine,
) -> None:
    database = PostgresLyraDB(postgis_engine)

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


def test_repeated_calls_reuse_the_pooled_engine(postgis_engine: Engine) -> None:
    database = PostgresLyraDB(postgis_engine)

    first = database.load_mesh_from_bounds(_INTERSECTING_BOUNDS)
    second = database.load_mesh_from_bounds(_INTERSECTING_BOUNDS)

    assert first.equals(second)
    assert postgis_engine.pool.status().startswith("Pool size: 1")
    _assert_no_checked_out_connections(postgis_engine)


def test_database_exception_returns_connection_to_pool(postgis_engine: Engine) -> None:
    database = PostgresLyraDB(postgis_engine)

    with pytest.raises(DatabaseError):
        database.load_census_from_bounds(
            _INTERSECTING_BOUNDS,
            level="mun",
            columns=["missing_column"],
        )

    _assert_no_checked_out_connections(postgis_engine)
    assert not database.load_mesh_from_bounds(_INTERSECTING_BOUNDS).empty
    _assert_no_checked_out_connections(postgis_engine)
