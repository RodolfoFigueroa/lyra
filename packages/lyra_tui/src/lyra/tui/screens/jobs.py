"""Screen for browsing and inspecting metric jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import timestamp_label, truncate
from lyra.tui.state import TuiSnapshot
from lyra.tui.widgets import EmptyState
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from typing_extensions import override

if TYPE_CHECKING:
    from lyra.sdk.models import JobStatusInfo
    from textual.app import ComposeResult

JOB_STATUS_PREFIXES = {
    "queued": "WAIT",
    "running": "RUN",
    "succeeded": "DONE",
    "failed": "FAIL",
    "cancelled": "STOP",
}


class JobsView(Vertical):
    """Recent job list with selection-aware status details."""

    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        """Initialize the jobs view with an optional snapshot."""
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._jobs: list[JobStatusInfo] = []
        self._ready = False

    @override
    def compose(self) -> ComposeResult:
        """Compose job summary, empty state, table, and details.

        Yields:
            Widgets forming the recent-jobs panel.
        """
        yield Static("", id="jobs-summary", classes="panel-summary")
        yield EmptyState("", widget_id="jobs-empty", classes="panel-message")
        yield DataTable(id="jobs-table")
        yield Static("", id="jobs-detail", classes="panel-summary")

    def on_mount(self) -> None:
        """Configure the job table and render the initial snapshot."""
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns("Job", "Status", "Metric", "Updated", "Error")
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        """Replace the displayed job collection."""
        self.snapshot = snapshot
        if not self._ready:
            return
        self._jobs = list(snapshot.jobs.jobs) if snapshot.jobs is not None else []
        self.query_one("#jobs-summary", Static).update(f"{len(self._jobs)} recent jobs")
        self.query_one("#jobs-empty", EmptyState).set_message(
            "No recent jobs." if snapshot.jobs is not None and not self._jobs else ""
        )
        table = self.query_one("#jobs-table", DataTable)
        table.clear()
        for job in self._jobs:
            table.add_row(*job_row(job), key=job.job_id)
        self.query_one("#jobs-detail", Static).update(
            job_detail_text(self._jobs[0]) if self._jobs else "No job selected."
        )

    def selected_job(self) -> JobStatusInfo | None:
        """Resolve the job selected by the table cursor.

        Returns:
            The selected job, the first job as fallback, or ``None`` when empty.
        """
        if not self._jobs:
            return None
        table = self.query_one("#jobs-table", DataTable)
        row_index = max(0, table.cursor_row)
        if row_index >= len(self._jobs):
            return self._jobs[0]
        return self._jobs[row_index]


def is_active_job_status(status: str) -> bool:
    """Check whether a job remains eligible for cancellation.

    Returns:
        Whether the status is queued or running.
    """
    return status in {"queued", "running"}


def job_row(job: JobStatusInfo) -> tuple[str, str, str, str, str]:
    """Format a job for the recent-jobs table.

    Returns:
        Job identifier, status, metric, update time, and error cells.
    """
    return (
        truncate(job.job_id, limit=24),
        job_status_label(job.status),
        truncate(job.metric or "unknown", limit=44),
        timestamp_label(job.updated_at),
        truncate(job.error or "", limit=48),
    )


def job_status_label(status: str) -> str:
    """Add a compact visual prefix to a job status.

    Returns:
        The prefixed status label.
    """
    prefix = JOB_STATUS_PREFIXES.get(status, "INFO")
    return f"{prefix} {status}"


def job_detail_text(job: JobStatusInfo) -> str:
    """Format the selected job's identifying details.

    Returns:
        A compact status line for the selected job.
    """
    metric = job.metric or "unknown metric"
    return (
        f"{job.job_id} | {job_status_label(job.status)} | "
        f"{metric} | {timestamp_label(job.updated_at)}"
    )
