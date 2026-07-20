from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import join_values, timestamp_label, truncate
from lyra.tui.state import TuiSnapshot
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class DashboardView(Vertical):
    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._ready = False

    def compose(self) -> ComposeResult:
        yield Static("", id="dashboard-summary", classes="panel-summary")
        yield DataTable(id="dashboard-table")

    def on_mount(self) -> None:
        table = self.query_one("#dashboard-table", DataTable)
        table.add_columns("Field", "Value")
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        self.snapshot = snapshot
        if not self._ready:
            return
        self.query_one("#dashboard-summary", Static).update(_summary_text(snapshot))
        table = self.query_one("#dashboard-table", DataTable)
        table.clear()
        for label, value in dashboard_rows(snapshot):
            table.add_row(label, value, key=label)


def dashboard_rows(snapshot: TuiSnapshot) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("Snapshot", snapshot.phase)]
    if snapshot.readiness is not None:
        rows.extend(
            [
                ("API status", snapshot.readiness.status),
                ("API version", snapshot.readiness.api_version),
                ("Redis status", snapshot.readiness.redis.status),
                ("Database status", snapshot.readiness.database.status),
            ]
        )
    else:
        rows.append(("API status", "unknown"))

    if snapshot.admin_status is None:
        rows.append(
            ("Admin", "locked" if snapshot.phase == "auth-required" else "unknown")
        )
    else:
        rows.extend(
            [
                ("Metric count", str(snapshot.admin_status.metric_count)),
                (
                    "Configured workers",
                    str(snapshot.admin_status.configured_worker_count),
                ),
                ("Default queue", snapshot.admin_status.default_queue),
                ("Allowed queues", join_values(snapshot.admin_status.allowed_queues)),
                ("Job TTL seconds", str(snapshot.admin_status.job_store_ttl_seconds)),
                (
                    "Catalog fingerprint",
                    truncate(snapshot.admin_status.catalog_fingerprint),
                ),
            ]
        )

    if snapshot.config_summary is not None:
        rows.extend(
            [
                (
                    "API bind",
                    f"{snapshot.config_summary.api_host}:{snapshot.config_summary.api_port}",
                ),
                ("Plugin state", truncate(snapshot.config_summary.plugin_state_path)),
            ]
        )
    if snapshot.catalog is not None:
        rows.append(("Loaded metrics", str(snapshot.catalog.metric_count)))
    if snapshot.last_updated is not None:
        rows.append(("Last refresh", timestamp_label(snapshot.last_updated)))
    if snapshot.errors:
        rows.append(("Latest issue", truncate(snapshot.errors[0].message)))
    return rows


def _summary_text(snapshot: TuiSnapshot) -> str:
    if snapshot.readiness is None:
        return "Readiness pending."
    if snapshot.admin_status is None:
        return "Public readiness available; admin data locked or unavailable."
    return (
        f"{snapshot.admin_status.metric_count} metrics | "
        f"{snapshot.admin_status.configured_worker_count} workers | "
        f"default queue {snapshot.admin_status.default_queue}"
    )
