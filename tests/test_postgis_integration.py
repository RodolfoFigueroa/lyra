import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import geopandas
import pytest
from lyra.sdk.db_types import Bounds
from lyra.sdk.postgres import PostgresLyraDB
from lyra.sdk.postgres_connection import (
    PostgresWorkload,
    apply_read_only_postgres_profile,
)
from pandas.errors import DatabaseError
from shapely.geometry import Point, Polygon
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError
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


@dataclass(frozen=True)
class _IntegrationRoles:
    schema: str
    owner: str
    runtime: str
    runtime_password: str


def _quoted_identifier(engine: Engine, value: str) -> str:
    return engine.dialect.identifier_preparer.quote_identifier(value)


def _provision_integration_database(
    admin_engine: Engine,
    admin_url: URL,
    roles: _IntegrationRoles,
) -> None:
    quoted_schema = _quoted_identifier(admin_engine, roles.schema)
    quoted_owner = _quoted_identifier(admin_engine, roles.owner)
    quoted_runtime = _quoted_identifier(admin_engine, roles.runtime)
    database_name = admin_url.database
    if database_name is None:
        msg = f"{_DATABASE_URL_VARIABLE} must include a database name"
        raise ValueError(msg)
    quoted_database = _quoted_identifier(admin_engine, database_name)

    with admin_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        connection.execute(text(f"CREATE ROLE {quoted_owner} NOLOGIN"))
        create_runtime_role = connection.execute(
            text(
                "SELECT format("
                "'CREATE ROLE %I LOGIN PASSWORD %L', "
                "CAST(:role AS text), CAST(:password AS text))"
            ),
            {"role": roles.runtime, "password": roles.runtime_password},
        ).scalar_one()
        connection.exec_driver_sql(create_runtime_role)
        connection.execute(CreateSchema(roles.schema))
        connection.execute(
            text(f"ALTER SCHEMA {quoted_schema} OWNER TO {quoted_owner}")
        )
        connection.execute(text(f"SET LOCAL ROLE {quoted_owner}"))
        connection.execute(text(f"SET LOCAL search_path TO {quoted_schema}, public"))
        for statement in _FIXTURE_SQL.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        connection.execute(text("RESET ROLE"))
        connection.execute(
            text(f"GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_runtime}")
        )
        connection.execute(
            text(f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_runtime}")
        )
        connection.execute(
            text(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA {quoted_schema} "
                f"TO {quoted_runtime}"
            )
        )
        connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {quoted_owner} "
                f"IN SCHEMA {quoted_schema} GRANT SELECT ON TABLES "
                f"TO {quoted_runtime}"
            )
        )
        connection.execute(
            text(f"ALTER ROLE {quoted_runtime} SET default_transaction_read_only = on")
        )


def _drop_integration_database(
    admin_engine: Engine,
    roles: _IntegrationRoles,
) -> None:
    quoted_owner = _quoted_identifier(admin_engine, roles.owner)
    quoted_runtime = _quoted_identifier(admin_engine, roles.runtime)
    with admin_engine.begin() as connection:
        connection.execute(DropSchema(roles.schema, cascade=True, if_exists=True))
        connection.execute(text(f"DROP OWNED BY {quoted_runtime}"))
        connection.execute(text(f"DROP OWNED BY {quoted_owner}"))
        connection.execute(text(f"DROP ROLE {quoted_runtime}"))
        connection.execute(text(f"DROP ROLE {quoted_owner}"))


@pytest.fixture(scope="module")
def postgis_database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get(_DATABASE_URL_VARIABLE)
    if database_url is None:
        pytest.skip(f"{_DATABASE_URL_VARIABLE} is not configured")

    suffix = uuid4().hex
    roles = _IntegrationRoles(
        schema=f"lyra_integration_{suffix}",
        owner=f"lyra_owner_{suffix}",
        runtime=f"lyra_runtime_{suffix}",
        runtime_password=f"runtime-{suffix}",
    )
    admin_url = make_url(database_url)
    admin_engine = create_engine(admin_url, hide_parameters=True)
    query_engine: Engine | None = None
    roles_provisioned = False

    try:
        _provision_integration_database(admin_engine, admin_url, roles)
        roles_provisioned = True

        runtime_url = admin_url.set(
            username=roles.runtime,
            password=roles.runtime_password,
        )
        runtime_url = apply_read_only_postgres_profile(
            runtime_url,
            workload=PostgresWorkload.INTEGRATION,
            statement_timeout_ms=25_000,
        )
        query_engine = create_engine(
            runtime_url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            pool_recycle=900,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        yield query_engine, roles.schema
    finally:
        if query_engine is not None:
            query_engine.dispose()
        if roles_provisioned:
            try:
                _drop_integration_database(admin_engine, roles)
            finally:
                admin_engine.dispose()
        else:
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


def test_runtime_role_has_read_only_connection_policy(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, schema = postgis_database

    with postgis_engine.connect() as connection:
        policy = connection.execute(
            text(
                "SELECT "
                "current_setting('transaction_read_only') AS read_only, "
                "current_setting('application_name') AS application_name, "
                "has_schema_privilege(current_user, :schema, 'USAGE') AS usage, "
                "has_schema_privilege(current_user, :schema, 'CREATE') AS create"
            ),
            {"schema": schema},
        ).one()
        privileges = connection.execute(
            text(
                "SELECT "
                "has_table_privilege("
                "current_user, :table_name, 'SELECT') AS select_allowed, "
                "has_table_privilege("
                "current_user, :table_name, 'INSERT,UPDATE,DELETE,TRUNCATE') "
                "AS writes_allowed"
            ),
            {"table_name": f"{schema}.census_2020_mun"},
        ).one()

    assert policy.read_only == "on"
    assert policy.application_name == "lyra-integration"
    assert policy.usage is True
    assert policy.create is False
    assert privileges.select_allowed is True
    assert privileges.writes_allowed is False


@pytest.mark.parametrize(
    "statement",
    [
        (
            "INSERT INTO {schema}.census_2020_mun "
            "(cvegeo, pobtot, geometry) VALUES "
            "('99999', 1, ST_GeomFromText("
            "'POLYGON ((1 1, 2 1, 2 2, 1 2, 1 1))', 6372))"
        ),
        "UPDATE {schema}.census_2020_mun SET pobtot = 0",
        "DELETE FROM {schema}.census_2020_mun",
        "CREATE TABLE {schema}.forbidden (value integer)",
        "ALTER TABLE {schema}.census_2020_mun ADD COLUMN forbidden integer",
    ],
)
def test_runtime_role_rejects_writes_and_schema_changes(
    postgis_database: tuple[Engine, str],
    statement: str,
) -> None:
    postgis_engine, schema = postgis_database
    quoted_schema = postgis_engine.dialect.identifier_preparer.quote_identifier(schema)

    with (
        pytest.raises(DBAPIError) as exc_info,
        postgis_engine.begin() as connection,
    ):
        connection.exec_driver_sql(statement.format(schema=quoted_schema))

    assert getattr(exc_info.value.orig, "sqlstate", None) in {"25006", "42501"}
    _assert_no_checked_out_connections(postgis_engine)


def test_connection_failure_does_not_expose_password(
    postgis_database: tuple[Engine, str],
) -> None:
    postgis_engine, _schema = postgis_database
    password = f"wrong-{uuid4().hex}"
    failed_url = postgis_engine.url.set(password=password)
    failed_engine = create_engine(
        failed_url,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )

    try:
        with pytest.raises(OperationalError) as exc_info:
            failed_engine.connect()
        assert password not in str(exc_info.value)
        assert password not in repr(exc_info.value)
        assert password not in str(failed_engine.url)
        assert password not in repr(failed_engine.url)
    finally:
        failed_engine.dispose()
