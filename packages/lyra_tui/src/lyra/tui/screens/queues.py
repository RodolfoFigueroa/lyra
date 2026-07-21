"""Screen for viewing metric routing and worker queues."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import bool_label, join_values
from lyra.tui.state import TuiSnapshot
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from typing_extensions import override

if TYPE_CHECKING:
    from lyra.sdk.models import QueueSummary
    from textual.app import ComposeResult


class QueuesView(Vertical):
    """Queue routing, worker coverage, and pending-depth panel."""

    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        """Initialize the queues view with an optional snapshot."""
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._ready = False

    @override
    def compose(self) -> ComposeResult:
        """Compose the queue summary and details table.

        Yields:
            The queue summary label and queue details table.
        """
        yield Static("", id="queues-summary", classes="panel-summary")
        yield DataTable(id="queues-table")

    def on_mount(self) -> None:
        """Configure the queue table and render the initial snapshot."""
        table = self.query_one("#queues-table", DataTable)
        table.add_columns(
            "Queue",
            "Default",
            "Metrics",
            "Configured workers",
            "Observed workers",
            "Pending",
        )
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        """Replace the displayed queue snapshot."""
        self.snapshot = snapshot
        if not self._ready:
            return
        queues = list(snapshot.queues.queues) if snapshot.queues is not None else []
        summary = "Queue data pending."
        if snapshot.queues is not None:
            summary = f"{len(queues)} queues | default {snapshot.queues.default_queue}"
        self.query_one("#queues-summary", Static).update(summary)
        table = self.query_one("#queues-table", DataTable)
        table.clear()
        for queue in queues:
            table.add_row(*queue_row(queue), key=queue.name)


def queue_row(queue: QueueSummary) -> tuple[str, str, str, str, str, str]:
    """Format queue routing and worker coverage for display.

    Returns:
        Name, default state, metrics, workers, and pending-depth cells.
    """
    return (
        queue.name,
        bool_label(value=queue.is_default),
        str(queue.assigned_metric_count),
        join_values(queue.configured_workers),
        join_values(queue.observed_workers),
        queue_depth_label(queue),
    )


def queue_depth_label(queue: QueueSummary) -> str:
    """Format a queue's optional pending depth.

    Returns:
        The depth or ``"unknown"`` when inspection data is unavailable.
    """
    if queue.pending_depth_unknown:
        return "unknown"
    if queue.pending_depth is None:
        return "unknown"
    return str(queue.pending_depth)
