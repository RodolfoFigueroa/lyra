"""Local runtime context for direct plugin execution."""

from __future__ import annotations

import importlib
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NotRequired, Self, TypedDict, cast

from lyra.sdk.context import RunCancelledError
from lyra.sdk.db import LyraDB, StubLyraDB
from lyra.sdk.models import JobMessageEvent, JobMessageLevel, JobProgressEvent
from lyra.sdk.run_context_state import RunProgressState
from typing_extensions import Unpack

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from contextlib import AbstractContextManager
    from pathlib import Path

    from sqlalchemy.engine import URL


class _LocalPostgresContextOptions(TypedDict):
    job_id: str
    metric: str
    temp_dir: Path
    schema: NotRequired[str]
    logger: NotRequired[logging.Logger | None]
    connect_timeout_seconds: NotRequired[float]
    pool_timeout_seconds: NotRequired[float]
    statement_timeout_ms: NotRequired[float]
    pool_recycle_seconds: NotRequired[float]


class LocalRunContext:
    """Provide deterministic SDK-only runtime services for a local plugin run.

    The caller owns ``temp_dir`` and its contents. Events remain available as
    tuple snapshots after direct plugin execution, and database access uses a
    strict :class:`StubLyraDB` unless an implementation is supplied.
    """

    def __init__(
        self,
        *,
        job_id: str,
        metric: str,
        temp_dir: Path,
        db: LyraDB | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize a local run context.

        Args:
            job_id: Non-empty identifier chosen by the caller.
            metric: Non-empty public metric name.
            temp_dir: Caller-owned directory for plugin files and outputs.
            db: Optional read-only database implementation.
            logger: Optional standard-library logger.

        Raises:
            NotADirectoryError: If ``temp_dir`` exists but is not a directory.
            ValueError: If ``job_id`` or ``metric`` is blank.
        """
        if not job_id.strip():
            msg = "job_id must be a non-empty string"
            raise ValueError(msg)
        if not metric.strip():
            msg = "metric must be a non-empty string"
            raise ValueError(msg)
        if temp_dir.exists() and not temp_dir.is_dir():
            msg = f"LocalRunContext temp_dir is not a directory: {temp_dir}"
            raise NotADirectoryError(msg)
        temp_dir.mkdir(parents=True, exist_ok=True)

        self._job_id = job_id
        self._metric = metric
        self._temp_dir = temp_dir
        self._db = db if db is not None else StubLyraDB()
        self._logger = logger or logging.getLogger("lyra.sdk.local")
        self._events: list[JobProgressEvent | JobMessageEvent] = []
        self._progress_state = RunProgressState()
        self._cancelled = False

    @classmethod
    @contextmanager
    def connect_postgres(
        cls,
        database_url: str | URL,
        **options: Unpack[_LocalPostgresContextOptions],
    ) -> Iterator[Self]:
        """Own a live local PostgreSQL context for the duration of one block.

        The context and its database must not be used after this manager exits.
        Ordinary :class:`LocalRunContext` instances with an injected database
        remain caller-owned and never dispose that database's resources.

        Yields:
            A local context backed by the shared PostgreSQL adapter.

        Raises:
            ImportError: If the ``lyra-sdk[postgres]`` extra is not installed.
            TypeError: If an unsupported local-context option is supplied.
        """
        unexpected = (
            options.keys() - _LocalPostgresContextOptions.__annotations__.keys()
        )
        if unexpected:
            names = ", ".join(sorted(unexpected))
            msg = f"unexpected local PostgreSQL context option(s): {names}"
            raise TypeError(msg)
        try:
            postgres = importlib.import_module("lyra.sdk.postgres")
        except ModuleNotFoundError as error:
            if "lyra-sdk[postgres]" in str(error):
                raise ImportError(str(error)) from None
            raise ImportError(str(error)) from error
        connector = cast(
            "Callable[..., AbstractContextManager[LyraDB]]",
            postgres.connect_postgres,
        )
        with connector(
            database_url,
            schema=options.get("schema", "public"),
            connect_timeout_seconds=options.get("connect_timeout_seconds", 5),
            pool_timeout_seconds=options.get("pool_timeout_seconds", 5),
            statement_timeout_ms=options.get("statement_timeout_ms", 300_000),
            pool_recycle_seconds=options.get("pool_recycle_seconds", 900),
        ) as database:
            yield cls(
                job_id=options["job_id"],
                metric=options["metric"],
                temp_dir=options["temp_dir"],
                db=database,
                logger=options.get("logger"),
            )

    @property
    def job_id(self) -> str:
        """The caller-provided job identifier."""
        return self._job_id

    @property
    def metric(self) -> str:
        """The metric being executed."""
        return self._metric

    @property
    def logger(self) -> logging.Logger:
        """The logger for local diagnostic details."""
        return self._logger

    @property
    def temp_dir(self) -> Path:
        """The caller-owned output directory."""
        return self._temp_dir

    @property
    def db(self) -> LyraDB:
        """The configured database implementation or a strict fresh stub."""
        return self._db

    @property
    def events(self) -> tuple[JobProgressEvent | JobMessageEvent, ...]:
        """All accepted events in report order."""
        return tuple(self._events)

    @property
    def progress_events(self) -> tuple[JobProgressEvent, ...]:
        """Accepted progress events in report order."""
        return tuple(
            event for event in self._events if isinstance(event, JobProgressEvent)
        )

    @property
    def message_events(self) -> tuple[JobMessageEvent, ...]:
        """Accepted message events in report order."""
        return tuple(
            event for event in self._events if isinstance(event, JobMessageEvent)
        )

    @property
    def cancelled(self) -> bool:
        """Whether cooperative cancellation has been requested."""
        return self._cancelled

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Validate and immediately capture one progress event."""
        event = JobProgressEvent(
            job_id=self.job_id,
            metric=self.metric,
            timestamp=datetime.now(UTC),
            stage=stage,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )
        self._progress_state.accept(event)
        self._events.append(event)

    def report_message(
        self,
        message: str,
        *,
        level: JobMessageLevel = "info",
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Validate and immediately capture one structured message event."""
        event = JobMessageEvent(
            job_id=self.job_id,
            metric=self.metric,
            timestamp=datetime.now(UTC),
            level=level,
            message=message,
            fields=fields or {},
        )
        self._events.append(event)

    def cancel(self) -> None:
        """Request cooperative cancellation of this local run."""
        self._cancelled = True

    def check_cancelled(self) -> None:
        """Raise after cooperative cancellation has been requested.

        Raises:
            RunCancelledError: If :meth:`cancel` has been called.
        """
        if self.cancelled:
            msg = f"Local run {self.job_id!r} was cancelled."
            raise RunCancelledError(msg)


__all__ = ["LocalRunContext"]
