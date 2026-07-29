"""Optional PostgreSQL implementation of the Lyra plugin database interface."""

from __future__ import annotations

import math
from contextlib import contextmanager
from numbers import Real
from typing import TYPE_CHECKING, Literal, NoReturn, TypedDict, TypeVar

from lyra.sdk.db import (
    DatabaseQueryError,
    DatabaseQueryTimeoutError,
    DatabaseUnavailableError,
    LyraDatabaseError,
    LyraDB,
)
from lyra.sdk.postgres_connection import (
    PostgresWorkload,
    apply_read_only_postgres_profile,
)
from lyra.sdk.postgres_sql import (
    DEFAULT_POSTGRES_SCHEMA,
    compile_postgres_query,
    postgres_table,
    validate_postgres_identifier,
)
from typing_extensions import Unpack

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from lyra.sdk.db_types import Bounds


def _raise_missing_postgres_extra(error: ModuleNotFoundError) -> NoReturn:
    if error.name is None or error.name.split(".", maxsplit=1)[0] not in {
        "geopandas",
        "psycopg",
        "sqlalchemy",
    }:
        raise error
    message = (
        "PostgreSQL support requires the 'postgres' extra. "
        "Install it with `uv add 'lyra-sdk[postgres]'`."
    )
    raise ModuleNotFoundError(message, name=error.name) from None


try:
    import geopandas
    import psycopg
except ModuleNotFoundError as error:
    _raise_missing_postgres_extra(error)

try:
    from sqlalchemy import Connection, bindparam, func, literal_column, select, text
    from sqlalchemy.engine import URL, Engine, create_engine, make_url
    from sqlalchemy.exc import ArgumentError, DBAPIError
    from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
except ModuleNotFoundError as error:
    _raise_missing_postgres_extra(error)

_DENUE_YEARS = frozenset({2020, 2021, 2022, 2023, 2024, 2025})
_DENUE_MONTHS = frozenset({5, 11})
_MESH_LEVELS = frozenset({4, 5, 6, 7, 8, 9})
_CENSUS_LEVELS = frozenset({"ent", "mun", "loc", "ageb", "mza"})
_POSTGRES_SERVER_UNAVAILABLE_STATES = frozenset(
    {
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now
        "57P04",  # database_dropped
    }
)
ResultT = TypeVar("ResultT")
_LOCAL_CONNECT_TIMEOUT_SECONDS = 5
_LOCAL_POOL_TIMEOUT_SECONDS = 5
_LOCAL_STATEMENT_TIMEOUT_MS = 300_000
_LOCAL_POOL_RECYCLE_SECONDS = 900


class _LocalPostgresOptions(TypedDict, total=False):
    schema: str
    connect_timeout_seconds: float
    pool_timeout_seconds: float
    statement_timeout_ms: float
    pool_recycle_seconds: float


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    """Return a finite cause/context chain for backend wrapper exceptions."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def classify_postgres_error(exc: BaseException) -> LyraDatabaseError:
    """Classify a PostgreSQL/SQLAlchemy failure into the public SDK taxonomy.

    SQLSTATE is authoritative when available. Driver exceptions without a
    SQLSTATE are considered unavailable only for Psycopg connection/interface
    failures; all unknown and deterministic failures are query errors.

    Returns:
        The existing SDK error or a safe SDK-owned classification.
    """
    chain = _exception_chain(exc)
    wrapped = next(
        (item for item in chain if isinstance(item, LyraDatabaseError)),
        None,
    )
    if isinstance(wrapped, LyraDatabaseError):
        return wrapped
    error_type: type[LyraDatabaseError]
    if any(isinstance(item, SQLAlchemyTimeoutError) for item in chain) or any(
        isinstance(item, DBAPIError) and item.connection_invalidated for item in chain
    ):
        error_type = DatabaseUnavailableError
    else:
        originals = tuple(
            item.orig if isinstance(item, DBAPIError) else item for item in chain
        )
        sqlstate = next(
            (
                state
                for item in originals
                if isinstance(state := getattr(item, "sqlstate", None), str)
            ),
            None,
        )
        if sqlstate == "57014":
            error_type = DatabaseQueryTimeoutError
        elif (
            isinstance(sqlstate, str)
            and (
                sqlstate.startswith(("08", "53"))
                or sqlstate in _POSTGRES_SERVER_UNAVAILABLE_STATES
            )
        ) or (
            sqlstate is None
            and any(
                isinstance(item, psycopg.OperationalError | psycopg.InterfaceError)
                for item in originals
            )
        ):
            error_type = DatabaseUnavailableError
        else:
            error_type = DatabaseQueryError
    return error_type()


def _run_database_operation(operation: Callable[[], ResultT]) -> ResultT:
    """Run one backend callable and translate failures at the SDK boundary.

    Returns:
        The backend operation result.
    """
    try:
        return operation()
    except Exception as exc:
        classified = classify_postgres_error(exc)
        if classified is exc:
            raise
        raise classified from exc


def _positive_number(value: float, *, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        or value <= 0
    ):
        msg = f"{field_name} must be a positive number"
        raise ValueError(msg)
    return value


def _local_postgres_url(database_url: str | URL) -> URL:
    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError):
        msg = "database_url must be a valid PostgreSQL URL"
        raise ValueError(msg) from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        msg = "database_url must use PostgreSQL with the Psycopg driver"
        raise ValueError(msg)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


@contextmanager
def connect_postgres(
    database_url: str | URL,
    **options: Unpack[_LocalPostgresOptions],
) -> Iterator[PostgresLyraDB]:
    """Own a local read-only PostgreSQL adapter and its single-connection pool.

    The yielded adapter is valid only inside this context manager. A lightweight
    ``SELECT 1`` probe verifies connectivity before it is yielded; no schema or
    compatibility metadata is inspected.

    Args:
        database_url: Password-bearing PostgreSQL URL or SQLAlchemy URL object.
        schema: Schema containing Lyra's shared data tables.
        connect_timeout_seconds: Deadline for establishing a connection.
        pool_timeout_seconds: Deadline for acquiring the pooled connection.
        statement_timeout_ms: Per-transaction PostgreSQL statement deadline.
        pool_recycle_seconds: Maximum age of a pooled connection.

    Yields:
        A live :class:`PostgresLyraDB` backed by an owned engine.

    Raises:
        TypeError: If an unsupported connection-profile option is supplied.
        ValueError: If the URL or local connection profile is invalid.
    """
    unexpected = options.keys() - _LocalPostgresOptions.__annotations__.keys()
    if unexpected:
        names = ", ".join(sorted(unexpected))
        msg = f"unexpected local PostgreSQL option(s): {names}"
        raise TypeError(msg)
    connect_timeout = _positive_number(
        options.get("connect_timeout_seconds", _LOCAL_CONNECT_TIMEOUT_SECONDS),
        field_name="connect_timeout_seconds",
    )
    pool_timeout = _positive_number(
        options.get("pool_timeout_seconds", _LOCAL_POOL_TIMEOUT_SECONDS),
        field_name="pool_timeout_seconds",
    )
    statement_timeout = _positive_number(
        options.get("statement_timeout_ms", _LOCAL_STATEMENT_TIMEOUT_MS),
        field_name="statement_timeout_ms",
    )
    pool_recycle = _positive_number(
        options.get("pool_recycle_seconds", _LOCAL_POOL_RECYCLE_SECONDS),
        field_name="pool_recycle_seconds",
    )
    url = apply_read_only_postgres_profile(
        _local_postgres_url(database_url),
        workload=PostgresWorkload.LOCAL,
        statement_timeout_ms=statement_timeout,
    )
    try:
        engine = create_engine(
            url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": connect_timeout},
        )
    except ArgumentError:
        msg = "database_url or local PostgreSQL profile is invalid"
        raise ValueError(msg) from None

    try:
        database = PostgresLyraDB(
            engine,
            schema=options.get("schema", DEFAULT_POSTGRES_SCHEMA),
        )

        def probe() -> None:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))

        _run_database_operation(probe)
        yield database
    finally:
        engine.dispose()


def _validate_bounds(bounds: Bounds) -> dict[str, float]:
    try:
        values = {
            "xmin": float(bounds.xmin),
            "ymin": float(bounds.ymin),
            "xmax": float(bounds.xmax),
            "ymax": float(bounds.ymax),
        }
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        msg = "bounds coordinates must be finite numbers"
        raise ValueError(msg) from exc
    if not all(math.isfinite(value) for value in values.values()):
        msg = "bounds coordinates must all be finite"
        raise ValueError(msg)
    if values["xmin"] >= values["xmax"]:
        msg = "bounds xmin must be less than xmax"
        raise ValueError(msg)
    if values["ymin"] >= values["ymax"]:
        msg = "bounds ymin must be less than ymax"
        raise ValueError(msg)
    return values


def _validate_columns(columns: Sequence[str]) -> list[str]:
    try:
        column_values = [columns] if isinstance(columns, str) else list(columns)
    except TypeError as exc:
        msg = "census columns must be a sequence of identifiers"
        raise ValueError(msg) from exc
    validated = [
        validate_postgres_identifier(column, field_name="census column")
        for column in column_values
    ]
    if len(validated) != len(set(validated)):
        msg = "census columns must be unique"
        raise ValueError(msg)
    if "geometry" not in validated:
        validated.append("geometry")
    return validated


def _load_geometries_from_bounds(
    *,
    bounds_parameters: dict[str, float],
    conn: Connection,
    columns: Sequence[str],
    table_name: str,
    schema: str,
) -> geopandas.GeoDataFrame:
    """Load geometries from a PostGIS table that intersect a bounding box.

    Returns:
        A GeoDataFrame of rows whose geometries intersect the given envelope.

    """
    table = postgres_table(
        table_name,
        schema=schema,
        columns=[*columns, "geometry"],
    )
    selected_columns = [table.c[column] for column in columns]
    geometry = table.c.geometry
    statement = (
        select(*selected_columns)
        .where(
            func.ST_Intersects(
                geometry,
                func.ST_MakeEnvelope(
                    bindparam("xmin"),
                    bindparam("ymin"),
                    bindparam("xmax"),
                    bindparam("ymax"),
                    literal_column("6372"),
                ),
            )
        )
        .order_by(
            *(table.c[column] for column in columns if column != "geometry"),
            func.ST_AsEWKB(geometry),
        )
    )
    return geopandas.read_postgis(
        compile_postgres_query(statement, conn),
        conn,
        params=bounds_parameters,
        geom_col="geometry",
    )


class PostgresLyraDB(LyraDB):
    """Implement the plugin database API using an injected synchronous engine."""

    def __init__(
        self,
        engine: Engine,
        *,
        schema: str = DEFAULT_POSTGRES_SCHEMA,
    ) -> None:
        """Initialize database operations with a runtime-owned engine."""
        self._schema = validate_postgres_identifier(
            schema,
            field_name="database schema",
        )
        self._engine = engine

    def load_denue_from_bounds(
        self,
        bounds: Bounds,
        *,
        year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
        month: Literal[5, 11],
    ) -> geopandas.GeoDataFrame:
        """Load DENUE economic-unit records that intersect a bounding box.

        DENUE (Directorio Estadístico Nacional de Unidades Económicas) tables are
        named ``denue_{year}_{month:02d}``. Returns the columns ``per_ocu``
        (employment size), ``codigo_act`` (activity code), and ``geometry``.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            year: Edition year of the DENUE dataset.
            month: Edition month of the DENUE dataset; either ``5`` (May) or
                ``11`` (November). Defaults to ``11``.

        Returns:
            A GeoDataFrame with columns ``["per_ocu", "codigo_act", "geometry"]``.

        Raises:
            ValueError: If the edition or bounds are invalid.

        """
        if (
            not isinstance(year, int)
            or isinstance(year, bool)
            or year not in _DENUE_YEARS
        ):
            msg = f"unsupported DENUE year: {year!r}"
            raise ValueError(msg)
        if (
            not isinstance(month, int)
            or isinstance(month, bool)
            or month not in _DENUE_MONTHS
        ):
            msg = f"unsupported DENUE month: {month!r}"
            raise ValueError(msg)
        parameters = _validate_bounds(bounds)

        def load() -> geopandas.GeoDataFrame:
            with self._engine.connect() as conn:
                return _load_geometries_from_bounds(
                    conn=conn,
                    columns=["per_ocu", "codigo_act", "geometry"],
                    table_name=f"denue_{year}_{month:02d}",
                    schema=self._schema,
                    bounds_parameters=parameters,
                )

        return _run_database_operation(load)

    def load_mesh_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal[4, 5, 6, 7, 8, 9] = 9,
    ) -> geopandas.GeoDataFrame:
        """Load mesh-grid cells that intersect a bounding box.

        Queries the ``mesh_level_{level}`` table and returns cells with their
        ``codigo`` identifier and geometry.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            level: Mesh resolution level (4-9). Higher values are finer.
                Defaults to ``9``.

        Returns:
            A GeoDataFrame with columns ``["codigo", "geometry"]``.

        Raises:
            ValueError: If the mesh level or bounds are invalid.

        """
        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or level not in _MESH_LEVELS
        ):
            msg = f"unsupported mesh level: {level!r}"
            raise ValueError(msg)
        parameters = _validate_bounds(bounds)

        def load() -> geopandas.GeoDataFrame:
            with self._engine.connect() as conn:
                return _load_geometries_from_bounds(
                    conn=conn,
                    columns=["codigo", "geometry"],
                    table_name=f"mesh_level_{level}",
                    schema=self._schema,
                    bounds_parameters=parameters,
                )

        return _run_database_operation(load)

    def load_census_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal["ent", "mun", "loc", "ageb", "mza"],
        columns: Sequence[str],
    ) -> geopandas.GeoDataFrame:
        """Load 2020 census records that intersect a bounding box.

        Queries the ``census_2020_{level}`` table for the specified geographic
        level and columns.

        Args:
            bounds: Minimum and maximum x/y coordinates to query.
            level: Geographic level of the census table. One of ``"ent"``
                (state), ``"mun"`` (municipality), ``"loc"`` (locality),
                ``"ageb"``, or ``"mza"`` (block).
            columns: Column names to select (``"geometry"`` is added if absent).

        Returns:
            A GeoDataFrame of census records intersecting the bounding box.

        Raises:
            ValueError: If the level, columns, or bounds are invalid.

        """
        if not isinstance(level, str) or level not in _CENSUS_LEVELS:
            msg = f"unsupported census level: {level!r}"
            raise ValueError(msg)
        validated_columns = _validate_columns(columns)
        parameters = _validate_bounds(bounds)

        def load() -> geopandas.GeoDataFrame:
            with self._engine.connect() as conn:
                return _load_geometries_from_bounds(
                    conn=conn,
                    columns=validated_columns,
                    table_name=f"census_2020_{level}",
                    schema=self._schema,
                    bounds_parameters=parameters,
                )

        return _run_database_operation(load)


__all__ = [
    "DEFAULT_POSTGRES_SCHEMA",
    "PostgresLyraDB",
    "classify_postgres_error",
    "connect_postgres",
]
