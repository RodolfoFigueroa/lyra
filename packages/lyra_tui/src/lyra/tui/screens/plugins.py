from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.screens.formatting import bool_label, truncate
from lyra.tui.state import TuiSnapshot
from textual.containers import Vertical
from textual.widgets import DataTable, Static

if TYPE_CHECKING:
    from lyra.sdk.models import PluginRepoResponse
    from textual.app import ComposeResult


class PluginsView(Vertical):
    def __init__(self, snapshot: TuiSnapshot | None = None) -> None:
        super().__init__()
        self.snapshot = snapshot or TuiSnapshot()
        self._repos: list[PluginRepoResponse] = []
        self._routes: list[tuple[str, str]] = []
        self._ready = False

    def compose(self) -> ComposeResult:
        yield Static("", id="plugins-summary", classes="panel-summary")
        yield Static("Plugin repos", classes="panel-message")
        yield DataTable(id="plugins-table")
        yield Static("Metric routing", classes="panel-message")
        yield DataTable(id="routing-table")

    def on_mount(self) -> None:
        repos = self.query_one("#plugins-table", DataTable)
        repos.add_columns("Repo", "Enabled", "Ref", "Source")
        routing = self.query_one("#routing-table", DataTable)
        routing.add_columns("Metric", "Queue")
        self._ready = True
        self.update_snapshot(self.snapshot)

    def update_snapshot(self, snapshot: TuiSnapshot) -> None:
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
        if not self._repos:
            return None
        table = self.query_one("#plugins-table", DataTable)
        row_index = max(0, table.cursor_row)
        if row_index >= len(self._repos):
            return self._repos[0]
        return self._repos[row_index]

    def selected_route(self) -> tuple[str, str] | None:
        if not self._routes:
            return None
        table = self.query_one("#routing-table", DataTable)
        row_index = max(0, table.cursor_row)
        if row_index >= len(self._routes):
            return self._routes[0]
        return self._routes[row_index]


def plugin_repo_row(repo: PluginRepoResponse) -> tuple[str, str, str, str]:
    return (
        repo.id,
        bool_label(value=repo.enabled),
        repo.ref or "none",
        truncate(repo.source),
    )


def routing_row(metric_name: str, queue: str) -> tuple[str, str]:
    return (truncate(metric_name), queue)
