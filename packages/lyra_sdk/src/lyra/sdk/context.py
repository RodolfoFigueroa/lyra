"""Runtime context supplied to metric implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.sdk.db import LyraDB


class RunContext(Protocol):
    """Runtime services and durable reporting hooks provided to a metric run."""

    @property
    def job_id(self) -> str:
        """The stable identifier of the job being executed."""
        ...

    @property
    def metric(self) -> str:
        """The public name of the metric being executed."""
        ...

    @property
    def logger(self) -> logging.Logger | logging.LoggerAdapter:
        """The logger for diagnostic, non-client-facing run details."""
        ...

    @property
    def temp_dir(self) -> Path:
        """The job-scoped directory for temporary files and outputs."""
        ...

    @property
    def db(self) -> LyraDB:
        """The read-only Lyra database client for this worker process."""
        ...

    def report_progress(
        self,
        *,
        stage: str,
        current: float,
        total: float | None = None,
        unit: str | None = None,
        message: str | None = None,
    ) -> None:
        """Publish quantitative progress for the current stage.

        Estimates, stages, and units may change between snapshots. Rapid
        updates are coalesced to the latest value.

        Args:
            stage: Stable name of the current unit of work.
            current: Non-negative amount completed in this stage.
            total: Positive amount that completes the stage, when known.
            unit: Human-readable unit for ``current`` and ``total``.
            message: Optional concise client-facing description of this update.

        Raises:
            ValueError: If the values are invalid.

        """
        ...
