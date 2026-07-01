from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import bool_label, count_label, join_values
from lyra.tui.state import TuiSnapshot
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from lyra.sdk.models import WorkerSummary
    from textual.app import ComposeResult


class WorkersView(Vertical):
    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._ready = False

    def compose(self) -> ComposeResult:
        yield Static("", id="workers-summary", classes="panel-summary")
        yield DataTable(id="workers-table")

    def on_mount(self) -> None:
        table = self.query_one("#workers-table", DataTable)
        table.add_columns(
            "Worker",
            "Status",
            "Configured",
            "Observed",
            "Queues",
            "Active",
            "Reserved",
            "Scheduled",
        )
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        self.snapshot = snapshot
        if not self._ready:
            return
        workers = list(snapshot.workers.workers) if snapshot.workers is not None else []
        summary = "Worker inspect pending."
        if snapshot.workers is not None:
            inspect = (
                "available" if snapshot.workers.inspect_available else "unavailable"
            )
            summary = f"{len(workers)} workers | inspect {inspect}"
        self.query_one("#workers-summary", Static).update(summary)
        table = self.query_one("#workers-table", DataTable)
        table.clear()
        for worker in workers:
            table.add_row(*worker_row(worker), key=worker.name)


def worker_row(worker: WorkerSummary) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        worker.name,
        worker_status_label(worker.status),
        bool_label(value=worker.configured),
        bool_label(value=worker.observed),
        join_values(worker.queues),
        count_label(worker.active_count),
        count_label(worker.reserved_count),
        count_label(worker.scheduled_count),
    )


def worker_status_label(status: str) -> str:
    prefixes = {
        "online": "OK",
        "offline": "OFF",
        "unknown": "UNK",
    }
    return f"{prefixes.get(status, 'UNK')} {status}"
