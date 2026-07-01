from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import timestamp_label, truncate
from lyra.tui.state import TuiSnapshot
from lyra.tui.widgets import EmptyState
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from lyra.sdk.models import JobStatusInfo
    from textual.app import ComposeResult

JOB_STATUS_PREFIXES = {
    "queued": "WAIT",
    "started": "RUN",
    "progress": "RUN",
    "succeeded": "DONE",
    "failed": "FAIL",
    "cancelled": "STOP",
}


class JobsView(Vertical):
    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._ready = False

    def compose(self) -> ComposeResult:
        yield Static("", id="jobs-summary", classes="panel-summary")
        yield EmptyState("", widget_id="jobs-empty", classes="panel-message")
        yield DataTable(id="jobs-table")
        yield Static("", id="jobs-detail", classes="panel-summary")

    def on_mount(self) -> None:
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns("Job", "Status", "Metric", "Updated", "Error")
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        self.snapshot = snapshot
        if not self._ready:
            return
        jobs = list(snapshot.jobs.jobs) if snapshot.jobs is not None else []
        self.query_one("#jobs-summary", Static).update(f"{len(jobs)} recent jobs")
        self.query_one("#jobs-empty", EmptyState).set_message(
            "No recent jobs." if snapshot.jobs is not None and not jobs else ""
        )
        table = self.query_one("#jobs-table", DataTable)
        table.clear()
        for job in jobs:
            table.add_row(*job_row(job), key=job.job_id)
        self.query_one("#jobs-detail", Static).update(
            job_detail_text(jobs[0]) if jobs else "No job selected."
        )


def job_row(job: JobStatusInfo) -> tuple[str, str, str, str, str]:
    return (
        truncate(job.job_id, limit=24),
        job_status_label(job.status),
        truncate(job.metric or "unknown", limit=44),
        timestamp_label(job.updated_at),
        truncate(job.error or "", limit=48),
    )


def job_status_label(status: str) -> str:
    prefix = JOB_STATUS_PREFIXES.get(status, "INFO")
    return f"{prefix} {status}"


def job_detail_text(job: JobStatusInfo) -> str:
    metric = job.metric or "unknown metric"
    return (
        f"{job.job_id} | {job_status_label(job.status)} | "
        f"{metric} | {timestamp_label(job.updated_at)}"
    )
