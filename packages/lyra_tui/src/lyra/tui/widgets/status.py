"""Widgets for displaying connection, loading, and action status."""

from __future__ import annotations

from lyra.tui.state import TuiSnapshot
from textual.widgets import Static


def format_snapshot_status(snapshot: TuiSnapshot) -> str:
    """Summarize snapshot health, authorization, and current errors.

    Returns:
        A compact connection-status line.
    """
    if snapshot.phase == "idle":
        return "Waiting for first refresh."
    if snapshot.phase == "loading":
        return "Refreshing Lyra status."

    readiness_text = "API unavailable"
    if snapshot.readiness is not None:
        readiness_text = (
            f"API {snapshot.readiness.status} "
            f"v{snapshot.readiness.api_version}; "
            f"Redis {snapshot.readiness.redis.status}; "
            f"database {snapshot.readiness.database.status}"
        )

    if snapshot.admin_status is not None:
        admin_text = (
            f"metrics {snapshot.admin_status.metric_count}; "
            f"workers {snapshot.admin_status.configured_worker_count}"
        )
    elif snapshot.phase == "auth-required":
        admin_text = "admin locked"
    else:
        admin_text = "admin unavailable"

    pieces = [readiness_text, admin_text]
    if snapshot.errors:
        pieces.append(snapshot.errors[0].message)
    return " | ".join(pieces)


class ConnectionStatus(Static):
    """Live one-line summary of API and administrative connectivity."""

    def __init__(
        self,
        snapshot: TuiSnapshot | None = None,
        *,
        widget_id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Initialize the widget from an optional service snapshot."""
        self.message = format_snapshot_status(snapshot or TuiSnapshot())
        super().__init__(self.message, id=widget_id, classes=classes, disabled=disabled)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        """Replace the displayed connection summary."""
        self.message = format_snapshot_status(snapshot)
        self.update(self.message)


class LoadingState(Static):
    """Reusable message shown while an operation is in progress."""

    def __init__(
        self,
        message: str = "Loading...",
        *,
        widget_id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Initialize a loading message widget."""
        self.message = message
        super().__init__(message, id=widget_id, classes=classes, disabled=disabled)


class EmptyState(Static):
    """Reusable message shown when a collection has no rows."""

    def __init__(
        self,
        message: str = "No data available.",
        *,
        widget_id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Initialize an empty-state message widget."""
        self.message = message
        super().__init__(message, id=widget_id, classes=classes, disabled=disabled)

    def set_message(self, message: str) -> None:
        """Replace the displayed empty-state message."""
        self.message = message
        self.update(message)


class ActionMessage(Static):
    """Status line for the most recent operator action."""

    def __init__(
        self,
        *,
        widget_id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        """Initialize an empty action-status widget."""
        self.message = ""
        super().__init__("", id=widget_id, classes=classes, disabled=disabled)

    def show_message(self, message: str) -> None:
        """Display the latest operator-action outcome."""
        self.message = message
        self.update(message)
