from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from lyra.sdk.db import LyraDB
    from lyra.sdk.models import JobMessageLevel


class RunContext(Protocol):
    """Runtime services and durable reporting hooks provided to a metric run."""

    @property
    def job_id(self) -> str:
        """Return the stable identifier of the job being executed."""
        ...

    @property
    def metric(self) -> str:
        """Return the public name of the metric being executed."""
        ...

    @property
    def logger(self) -> logging.Logger:
        """Return the logger for diagnostic, non-client-facing run details."""
        ...

    @property
    def temp_dir(self) -> Path:
        """Return the job-scoped directory for temporary files and outputs."""
        ...

    @property
    def db(self) -> LyraDB:
        """Return the read-only Lyra database client for this worker process."""
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

        Progress must be monotonic within a stage. Once provided, ``total`` and
        ``unit`` must remain stable until the stage changes. The worker may
        coalesce rapid intermediate updates while retaining stage boundaries
        and completed progress.

        Args:
            stage: Stable name of the current unit of work.
            current: Non-negative amount completed in this stage.
            total: Positive amount that completes the stage, when known.
            unit: Human-readable unit for ``current`` and ``total``.
            message: Optional concise client-facing description of this update.

        Raises:
            ValueError: If the values are invalid or progress regresses.

        """
        ...

    def report_message(
        self,
        message: str,
        *,
        level: JobMessageLevel = "info",
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Publish a durable structured message for clients observing the job.

        Use :attr:`logger` for diagnostic detail that does not belong in the
        client-visible job event stream.

        Args:
            message: Concise client-facing message text.
            level: Severity used by clients and structured logs.
            fields: Optional JSON-compatible structured context.

        Raises:
            ValueError: If the message or encoded event exceeds runtime limits.

        """
        ...

    def check_cancelled(self) -> None:
        """Raise when cancellation has been requested for the current job.

        Plugins should call this method around expensive or repeated stages so
        cancellation remains cooperative and responsive.

        Raises:
            RuntimeError: If the current job has been cancelled.

        """
        ...


__all__ = ["RunContext"]
