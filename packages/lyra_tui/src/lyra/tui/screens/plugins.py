"""Screen for inspecting and managing installed plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import bool_label, truncate
from lyra.tui.state import TuiSnapshot
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from typing_extensions import override

if TYPE_CHECKING:
    from lyra.sdk.models import PluginRepoResponse
    from textual.app import ComposeResult


class PluginsView(Vertical):
    """Plugin repository and metric-routing management panel."""

    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        """Initialize the plugin view with an optional snapshot."""
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._repos: list[PluginRepoResponse] = []
        self._routes: list[tuple[str, str]] = []
        self._ready = False

    @override
    def compose(self) -> ComposeResult:
        """Compose repository and metric-routing tables.

        Yields:
            Summary, command hints, and catalog management tables.
        """
        yield Static("", id="plugins-summary", classes="panel-summary")
        yield Static("Plugin repos", classes="panel-message")
        yield Static("", id="plugins-actions", classes="catalog-action-bar")
        yield DataTable(id="plugins-table")
        yield Static("Metric routing", classes="panel-message")
        yield Static("", id="routing-actions", classes="catalog-action-bar")
        yield DataTable(id="routing-table")

    def on_mount(self) -> None:
        """Configure both catalog tables and render the initial snapshot."""
        repos = self.query_one("#plugins-table", DataTable)
        repos.add_columns("Repo", "Enabled", "Ref", "Source")
        routing = self.query_one("#routing-table", DataTable)
        routing.add_columns("Metric", "Queue")
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
        """Replace displayed repository and routing state."""
        self.snapshot = snapshot
        if not self._ready:
            return
        repo_count = len(snapshot.plugin_repos.repos) if snapshot.plugin_repos else 0
        route_count = (
            len(snapshot.plugin_routing.metric_queues)
            if snapshot.plugin_routing is not None
            else 0
        )
        metric_count = snapshot.catalog.metric_count if snapshot.catalog else "unknown"
        self.query_one("#plugins-summary", Static).update(
            f"{metric_count} loaded metrics | {repo_count} repos | {route_count} routes"
        )
        self.query_one("#plugins-actions", Static).update(repo_actions_text(repo_count))
        self.query_one("#routing-actions", Static).update(
            routing_actions_text(route_count)
        )

        repos = self.query_one("#plugins-table", DataTable)
        repos.clear()
        self._repos = []
        if snapshot.plugin_repos is not None:
            self._repos = list(snapshot.plugin_repos.repos)
            for repo in snapshot.plugin_repos.repos:
                repos.add_row(*plugin_repo_row(repo), key=repo.id)

        routing = self.query_one("#routing-table", DataTable)
        routing.clear()
        self._routes = []
        if snapshot.plugin_routing is not None:
            self._routes = sorted(snapshot.plugin_routing.metric_queues.items())
            for metric_name, queue in self._routes:
                routing.add_row(*routing_row(metric_name, queue), key=metric_name)

    def selected_repo(self) -> PluginRepoResponse | None:
        """Resolve the plugin repository selected by the table cursor.

        Returns:
            The selected repository, a fallback repository, or ``None``.
        """
        if not self._repos:
            return None
        table = self.query_one("#plugins-table", DataTable)
        row_index = max(0, table.cursor_row)
        if row_index >= len(self._repos):
            return self._repos[0]
        return self._repos[row_index]

    def selected_route(self) -> tuple[str, str] | None:
        """Resolve the metric route selected by the table cursor.

        Returns:
            The selected metric and queue pair, a fallback pair, or ``None``.
        """
        if not self._routes:
            return None
        table = self.query_one("#routing-table", DataTable)
        row_index = max(0, table.cursor_row)
        if row_index >= len(self._routes):
            return self._routes[0]
        return self._routes[row_index]


def plugin_repo_row(repo: PluginRepoResponse) -> tuple[str, str, str, str]:
    """Format a repository for the plugin table.

    Returns:
        Repository identifier, enabled state, revision, and source cells.
    """
    return (
        repo.id,
        bool_label(value=repo.enabled),
        repo.ref or "none",
        truncate(repo.source),
    )


def routing_row(metric_name: str, queue: str) -> tuple[str, str]:
    """Format a metric routing assignment for display.

    Returns:
        The bounded metric name and assigned queue.
    """
    return (truncate(metric_name), queue)


def repo_actions_text(repo_count: int) -> str:
    """Build context-sensitive repository command hints.

    Returns:
        Rich-markup command hints for the repository table.
    """
    actions = [
        "[reverse] a [/reverse] Add repo",
        "[reverse] p [/reverse] Refresh catalog",
    ]
    if repo_count:
        actions.extend(
            [
                "[reverse] e [/reverse] Enable/Disable",
                "[reverse] s [/reverse] Sync",
                "[reverse] d [/reverse] Delete",
            ]
        )
    actions.append(
        "[reverse] tab [/reverse] [reverse] shift+tab [/reverse] Switch table"
    )
    return "[b]Repo commands[/b]  " + "  ".join(actions)


def routing_actions_text(route_count: int) -> str:
    """Build context-sensitive routing command hints.

    Returns:
        Rich-markup command hints for the routing table.
    """
    actions = ["[reverse] m [/reverse] Assign route"]
    if route_count:
        actions.append("[reverse] x [/reverse] Delete route")
    actions.append(
        "[reverse] tab [/reverse] [reverse] shift+tab [/reverse] Switch table"
    )
    return "[b]Routing commands[/b]  " + "  ".join(actions)
