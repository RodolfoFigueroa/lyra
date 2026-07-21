"""Database connection management and transaction primitives."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import URL, Engine, create_engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from lyra_app.config import DatabasePoolConfig, LyraConfig, get_config

if TYPE_CHECKING:
    from collections.abc import Callable

ResultT = TypeVar("ResultT")
Parameters = ParamSpec("Parameters")


class DatabaseUnavailableError(RuntimeError):
    """Raised when database work cannot start within its service deadline."""


def database_url(
    drivername: str = "postgresql+psycopg",
    config: LyraConfig | None = None,
) -> URL:
    """Build a password-bearing SQLAlchemy URL from validated runtime config.

    Returns:
        A PostgreSQL URL using the selected SQLAlchemy driver.
    """
    config = get_config() if config is None else config
    return URL.create(
        drivername,
        username=config.database.user,
        password=config.database.read_password(),
        host=config.database.host,
        port=config.database.port,
        database=config.database.name,
    )


def _engine_options(
    pool: DatabasePoolConfig,
) -> dict[str, bool | int | float | dict[str, int | str]]:
    return {
        "pool_size": pool.pool_size,
        "max_overflow": pool.max_overflow,
        "pool_timeout": pool.pool_timeout_seconds,
        "pool_recycle": pool.pool_recycle_seconds,
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": pool.connect_timeout_seconds,
            "options": f"-c statement_timeout={pool.statement_timeout_ms}",
        },
    }


def create_sync_database_engine(
    pool: DatabasePoolConfig,
    config: LyraConfig | None = None,
) -> Engine:
    """Create a synchronous SQLAlchemy engine for one configured workload pool.

    Returns:
        A lazily connecting synchronous database engine.
    """
    return create_engine(database_url(config=config), **_engine_options(pool))


def create_async_database_engine(
    pool: DatabasePoolConfig,
    config: LyraConfig | None = None,
) -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine for one configured workload pool.

    Returns:
        A lazily connecting asynchronous database engine.
    """
    return create_async_engine(database_url(config=config), **_engine_options(pool))


class ApplicationDatabaseRuntime:
    """Own the API process database engines and bounded spatial executor."""

    def __init__(self, config: LyraConfig) -> None:
        """Initialize stopped API database resources for a validated config."""
        self.config = config
        self.async_engine: AsyncEngine | None = None
        self.spatial_engine: Engine | None = None
        self._spatial_executor: ThreadPoolExecutor | None = None
        self._spatial_capacity: asyncio.Semaphore | None = None

    async def start(self) -> None:
        """Create API engines and the bounded spatial-query executor once."""
        if self.async_engine is not None:
            return
        self.async_engine = create_async_database_engine(
            self.config.database.api,
            self.config,
        )
        self.spatial_engine = create_sync_database_engine(
            self.config.database.spatial,
            self.config,
        )
        worker_count = self.config.database.spatial.pool_size
        self._spatial_executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="lyra-spatial",
        )
        self._spatial_capacity = asyncio.Semaphore(worker_count)

    async def close(self) -> None:
        """Stop the spatial executor and dispose all application engines."""
        async_engine = self.async_engine
        spatial_engine = self.spatial_engine
        executor = self._spatial_executor
        self.async_engine = None
        self.spatial_engine = None
        self._spatial_executor = None
        self._spatial_capacity = None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
        if spatial_engine is not None:
            await asyncio.to_thread(spatial_engine.dispose)
        if async_engine is not None:
            await async_engine.dispose()

    def require_async_engine(self) -> AsyncEngine:
        """Return the started asynchronous API engine.

        Returns:
            The engine owned by this runtime.

        Raises:
            RuntimeError: If the runtime has not been started.
        """
        if self.async_engine is None:
            msg = "Application database runtime has not been started."
            raise RuntimeError(msg)
        return self.async_engine

    def require_spatial_engine(self) -> Engine:
        """Return the started synchronous spatial engine.

        Returns:
            The spatial engine owned by this runtime.

        Raises:
            RuntimeError: If the runtime has not been started.
        """
        if self.spatial_engine is None:
            msg = "Application database runtime has not been started."
            raise RuntimeError(msg)
        return self.spatial_engine

    async def run_spatial(
        self,
        function: Callable[Parameters, ResultT],
        /,
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> ResultT:
        """Run synchronous spatial work within bounded executor capacity.

        Returns:
            The value returned by ``function``.

        Raises:
            RuntimeError: If the application database runtime is not started.
            DatabaseUnavailableError: If spatial capacity cannot be acquired before
                the configured pool deadline.
        """
        capacity = self._spatial_capacity
        executor = self._spatial_executor
        if capacity is None or executor is None:
            msg = "Application database runtime has not been started."
            raise RuntimeError(msg)

        try:
            async with asyncio.timeout(
                self.config.database.spatial.pool_timeout_seconds
            ):
                await capacity.acquire()
        except TimeoutError as exc:
            msg = "Spatial database capacity is temporarily unavailable."
            raise DatabaseUnavailableError(msg) from exc

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                executor,
                partial(function, *args, **kwargs),
            )
        finally:
            capacity.release()


_worker_engine: Engine | None = None
_worker_engine_pid: int | None = None


def get_worker_engine(config: LyraConfig | None = None) -> Engine:
    """Return the process-local worker engine, rebuilding it after a fork.

    Returns:
        The cached or newly created synchronous worker engine.
    """
    global _worker_engine, _worker_engine_pid  # ruff:ignore[global-statement]

    process_id = os.getpid()
    if _worker_engine is not None and _worker_engine_pid != process_id:
        _worker_engine.dispose(close=False)
        _worker_engine = None
    if _worker_engine is None:
        runtime_config = get_config() if config is None else config
        _worker_engine = create_sync_database_engine(
            runtime_config.database.worker,
            runtime_config,
        )
        _worker_engine_pid = process_id
    return _worker_engine


def probe_worker_database(config: LyraConfig) -> None:
    """Verify worker database connectivity without retaining a pre-fork engine."""
    engine = create_sync_database_engine(config.database.worker, config)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def dispose_worker_engine() -> None:
    """Dispose and clear the process-local worker database engine."""
    global _worker_engine, _worker_engine_pid  # ruff:ignore[global-statement]

    if _worker_engine is not None:
        _worker_engine.dispose()
    _worker_engine = None
    _worker_engine_pid = None


def is_database_unavailable_error(exc: BaseException) -> bool:
    """Classify transient connection, capacity, and statement-timeout failures.

    Returns:
        ``True`` when callers should present a temporary-unavailability response.
    """
    if isinstance(
        exc,
        DatabaseUnavailableError | OperationalError | SQLAlchemyTimeoutError,
    ):
        return True
    if isinstance(exc, DBAPIError):
        if exc.connection_invalidated:
            return True
        sqlstate = getattr(exc.orig, "sqlstate", None)
        return sqlstate == "57014"
    return False


__all__ = [
    "ApplicationDatabaseRuntime",
    "DatabaseUnavailableError",
    "create_async_database_engine",
    "create_sync_database_engine",
    "database_url",
    "dispose_worker_engine",
    "get_worker_engine",
    "is_database_unavailable_error",
    "probe_worker_database",
]
