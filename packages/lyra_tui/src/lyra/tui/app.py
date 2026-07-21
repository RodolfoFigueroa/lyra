"""Textual application and screen coordination for the Lyra TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lyra.tui.actions import ActionResult, ActionService
from lyra.tui.client import LyraApiClientAdapter
from lyra.tui.screens import (
    DashboardView,
    JobsView,
    PluginsView,
    QueuesView,
    WorkersView,
)
from lyra.tui.screens.jobs import is_active_job_status
from lyra.tui.state import LyraTuiState, TuiSnapshot
from lyra.tui.widgets import (
    ActionMessage,
    ConfirmDialog,
    ConnectionStatus,
    PluginRepoDialog,
    PluginRepoForm,
    RestartWorkersDialog,
    RoutingDialog,
    RoutingForm,
)
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, TabbedContent, TabPane, Tabs

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from typing import ClassVar

    from lyra.sdk.models import JobStatusInfo, PluginRepoResponse
    from lyra.tui.client import LyraTuiClient
    from lyra.tui.config import TuiConfig
    from lyra.tui.state import SnapshotPhase
    from textual.worker import Worker


CATALOG_TAB_ID = "catalog"
CATALOG_TAB_REQUIRED_MESSAGE = (
    "Catalog actions are only available from the Catalog tab."
)


class LyraTuiApp(App[None]):  # ruff: ignore[too-many-public-methods] -- Textual actions
    """Minimal Lyra operator console shell."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        height: auto;
        padding: 0 1;
    }

    #action-message {
        height: auto;
        padding: 0 1;
    }

    TabbedContent {
        height: 1fr;
    }

    DataTable {
        height: 1fr;
    }

    .dialog-screen {
        align: center middle;
    }

    .panel-summary {
        height: auto;
        padding: 0 1;
    }

    .panel-message {
        height: auto;
        padding: 0 1;
    }

    .catalog-action-bar {
        height: auto;
        padding: 0 1;
        background: $surface-lighten-1;
        color: $text;
        text-style: bold;
    }

    .dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        margin: 1 2;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    .dialog-title {
        text-style: bold;
        height: auto;
        margin-bottom: 1;
    }

    .dialog-body,
    .dialog-fields,
    .dialog-error {
        height: auto;
        margin-bottom: 1;
    }

    .dialog-error {
        color: $error;
    }

    .dialog-buttons {
        height: auto;
        align-horizontal: right;
    }

    .dialog-buttons Button {
        min-width: 8;
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "refresh", "Refresh"),
        Binding("c", "cancel_job", "Cancel Job"),
        Binding("w", "restart_workers", "Restart Workers"),
        Binding("p", "refresh_catalog", "Refresh Catalog", show=False),
        Binding("a", "add_plugin_repo", "Add Repo", show=False),
        Binding("e", "toggle_plugin_repo", "Enable/Disable Repo", show=False),
        Binding("d", "delete_plugin_repo", "Delete Repo", show=False),
        Binding("s", "sync_plugin_repo", "Sync Repo", show=False),
        Binding("m", "assign_route", "Assign Route", show=False),
        Binding("x", "delete_route", "Delete Route", show=False),
        Binding("enter", "focus_active_tab_content", "Enter Section"),
        Binding("escape", "focus_tab_strip", "Exit Section"),
        Binding("tab", "focus_next_table", "Next Table", show=False),
        Binding("shift+tab", "focus_previous_table", "Previous Table", show=False),
        Binding("q", "quit_app", "Quit", priority=True),
    ]

    def __init__(
        self,
        config: TuiConfig,
        *,
        state: LyraTuiState | None = None,
        poll_on_mount: bool = True,
    ) -> None:
        """Initialize the TUI with its configuration, state, and polling policy."""
        super().__init__()
        self.config = config
        self.state = state or LyraTuiState(
            LyraApiClientAdapter(config),
            has_admin_key=config.has_admin_key,
        )
        self.action_service = ActionService(cast("LyraTuiClient", self.state.client))
        self.poll_on_mount = poll_on_mount
        self._refresh_timer = None
        self._refresh_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        """Compose the status bar, tabbed operational views, and footer.

        Yields:
            Widgets forming the application shell and operational tabs.
        """
        yield Header(show_clock=False)
        yield ConnectionStatus(self.state.snapshot, widget_id="status")
        yield ActionMessage(widget_id="action-message")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardView(self.state.snapshot)
            with TabPane("Jobs", id="jobs"):
                yield JobsView(self.state.snapshot)
            with TabPane("Workers", id="workers"):
                yield WorkersView(self.state.snapshot)
            with TabPane("Queues", id="queues"):
                yield QueuesView(self.state.snapshot)
            with TabPane("Catalog", id="catalog"):
                yield PluginsView(self.state.snapshot)
        yield Footer()

    def on_mount(self) -> None:
        """Start immediate and periodic snapshot refreshes when polling is enabled."""
        if not self.poll_on_mount:
            return
        self.request_refresh()
        self._refresh_timer = self.set_interval(
            self.config.refresh_interval,
            self.request_refresh,
            name="refresh",
        )

    def on_unmount(self) -> None:
        """Stop polling and cancel outstanding refresh and action workers."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self.workers.cancel_group(self, "refresh")
        self.workers.cancel_group(self, "action")

    def on_tabbed_content_tab_activated(
        self,
        _event: TabbedContent.TabActivated,
    ) -> None:
        """Refresh available key bindings after the active tab changes."""
        self.call_after_refresh(self.screen.refresh_bindings)

    def _check_operator_action(self, action: str) -> bool | None:
        if action in {"refresh_catalog", "add_plugin_repo", "assign_route"}:
            return self.config.has_admin_key and self._catalog_tab_is_active()
        if action == "restart_workers":
            return self.config.has_admin_key
        if action in {
            "toggle_plugin_repo",
            "delete_plugin_repo",
            "sync_plugin_repo",
        }:
            return (
                self.config.has_admin_key
                and self._catalog_tab_is_active()
                and self._selected_repo() is not None
            )
        if action == "delete_route":
            return (
                self.config.has_admin_key
                and self._catalog_tab_is_active()
                and self._selected_route() is not None
            )
        return None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Return whether an action is currently available for the UI state."""
        del parameters
        try:
            return self._resolve_action_state(action)
        except Exception:  # ruff:ignore[blind-except]
            return None

    def _resolve_action_state(self, action: str) -> bool | None:
        if action == "quit_app":
            return True
        if action == "focus_active_tab_content":
            return (
                self._tab_strip_is_focused()
                and self._active_tab_focus_target() is not None
            )
        if action == "focus_tab_strip":
            return self._table_is_focused()
        if action in {"focus_next_table", "focus_previous_table"}:
            return self._table_is_focused() and len(self._active_tab_tables()) > 1
        if action == "cancel_job":
            job = self._selected_job()
            return job is not None and is_active_job_status(job.status)
        return self._check_operator_action(action)

    def request_refresh(self) -> None:
        """Start one snapshot refresh unless a prior refresh is still running."""
        if self._refresh_worker is not None and not self._refresh_worker.is_finished:
            return
        self.show_snapshot(_loading_snapshot(self.state.snapshot))
        self._refresh_worker = self.run_worker(
            self.refresh_once(),
            name="refresh",
            group="refresh",
            exit_on_error=False,
        )

    def action_refresh(self) -> None:
        """Request an immediate service-state refresh."""
        self.request_refresh()

    def action_quit_app(self) -> None:
        """Exit the terminal application."""
        self.exit()

    def action_focus_active_tab_content(self) -> None:
        """Move focus from the tab strip into the active tab's primary control."""
        if not self._tab_strip_is_focused():
            return
        focus_target = self._active_tab_focus_target()
        if focus_target is not None:
            focus_target.focus()

    def action_focus_tab_strip(self) -> None:
        """Move focus from a data table back to the tab strip."""
        if self._table_is_focused():
            self.query_one(Tabs).focus()

    def action_focus_next_table(self) -> None:
        """Move focus cyclically to the next table in the active tab."""
        self._focus_relative_table(1)

    def action_focus_previous_table(self) -> None:
        """Move focus cyclically to the previous table in the active tab."""
        self._focus_relative_table(-1)

    def action_cancel_job(self) -> None:
        """Confirm cancellation of the selected active job."""
        job = self._selected_job()
        if job is None:
            self.show_action_message("No job selected.")
            return
        if not is_active_job_status(job.status):
            self.show_action_message(f"Job is already terminal: {job.status}.")
            return
        self.push_screen(
            ConfirmDialog(
                "Cancel job",
                f"Cancel {job.job_id} ({job.status})?",
                confirm_label="Cancel job",
            ),
            callback=lambda confirmed: self._cancel_job_after_confirm(
                job,
                confirmed=confirmed,
            ),
        )

    def action_restart_workers(self) -> None:
        """Open the worker restart form."""
        self.push_screen(
            RestartWorkersDialog(timeout=30.0),
            callback=self._restart_workers_after_dialog,
        )

    def action_refresh_catalog(self) -> None:
        """Confirm synchronization and refresh of the plugin catalog."""
        if not self._require_catalog_tab():
            return
        self.push_screen(
            ConfirmDialog(
                "Refresh catalog",
                "Sync enabled plugin sources and refresh the API catalog?",
                confirm_label="Refresh",
            ),
            callback=lambda confirmed: self._refresh_catalog_after_confirm(
                confirmed=confirmed,
            ),
        )

    def action_add_plugin_repo(self) -> None:
        """Open the form for adding a plugin repository."""
        if not self._require_catalog_tab():
            return
        self.push_screen(
            PluginRepoDialog(),
            callback=self._add_plugin_repo_after_dialog,
        )

    def action_toggle_plugin_repo(self) -> None:
        """Confirm enabling or disabling the selected plugin repository."""
        if not self._require_catalog_tab():
            return
        repo = self._selected_repo()
        if repo is None:
            self.show_action_message("No plugin repo selected.")
            return
        action = "disable" if repo.enabled else "enable"
        self.push_screen(
            ConfirmDialog(
                f"{action.title()} plugin repo",
                f"{action.title()} {repo.id}?",
                confirm_label=action.title(),
            ),
            callback=lambda confirmed: self._toggle_plugin_repo_after_confirm(
                repo,
                confirmed=confirmed,
            ),
        )

    def action_delete_plugin_repo(self) -> None:
        """Confirm deletion of the selected plugin repository."""
        if not self._require_catalog_tab():
            return
        repo = self._selected_repo()
        if repo is None:
            self.show_action_message("No plugin repo selected.")
            return
        self.push_screen(
            ConfirmDialog(
                "Delete plugin repo",
                f"Delete plugin repo {repo.id}?",
                confirm_label="Delete",
            ),
            callback=lambda confirmed: self._delete_plugin_repo_after_confirm(
                repo,
                confirmed=confirmed,
            ),
        )

    def action_sync_plugin_repo(self) -> None:
        """Confirm synchronization of the selected plugin repository."""
        if not self._require_catalog_tab():
            return
        repo = self._selected_repo()
        if repo is None:
            self.show_action_message("No plugin repo selected.")
            return
        self.push_screen(
            ConfirmDialog(
                "Sync plugin repo",
                f"Sync plugin repo {repo.id}?",
                confirm_label="Sync",
            ),
            callback=lambda confirmed: self._sync_plugin_repo_after_confirm(
                repo,
                confirmed=confirmed,
            ),
        )

    def action_assign_route(self) -> None:
        """Open a form to assign the selected metric to an allowed queue."""
        if not self._require_catalog_tab():
            return
        allowed_queues = self._allowed_queues()
        if not allowed_queues:
            self.show_action_message("No allowed queues are available.")
            return
        route = self._selected_route()
        metric_name = route[0] if route is not None else None
        self.push_screen(
            RoutingDialog(
                allowed_queues=allowed_queues,
                metric_name=metric_name,
            ),
            callback=self._assign_route_after_dialog,
        )

    def action_delete_route(self) -> None:
        """Confirm deletion of the selected explicit metric route."""
        if not self._require_catalog_tab():
            return
        route = self._selected_route()
        if route is None:
            self.show_action_message("No route selected.")
            return
        metric_name, queue = route
        self.push_screen(
            ConfirmDialog(
                "Delete metric route",
                f"Delete explicit route for {metric_name} ({queue})?",
                confirm_label="Delete",
            ),
            callback=lambda confirmed: self._delete_route_after_confirm(
                metric_name,
                confirmed=confirmed,
            ),
        )

    async def refresh_once(self) -> None:
        """Fetch one state snapshot and apply it to every view."""
        snapshot = await self.state.refresh()
        self.show_snapshot(snapshot)

    def show_snapshot(self, snapshot: TuiSnapshot) -> None:
        """Store a snapshot and propagate it to status and tab views."""
        self.state.snapshot = snapshot
        self.query_one(ConnectionStatus).update_snapshot(snapshot)
        for view in self.query(DashboardView):
            view.update_snapshot(snapshot)
        for view in self.query(JobsView):
            view.update_snapshot(snapshot)
        for view in self.query(WorkersView):
            view.update_snapshot(snapshot)
        for view in self.query(QueuesView):
            view.update_snapshot(snapshot)
        for view in self.query(PluginsView):
            view.update_snapshot(snapshot)

    def show_action_message(self, message: str) -> None:
        """Display feedback from the latest administrative action."""
        self.query_one(ActionMessage).show_message(message)

    def _run_action(self, awaitable: Awaitable[ActionResult]) -> None:
        self.run_worker(
            self._finish_action(awaitable),
            name="action",
            group="action",
            exit_on_error=False,
        )

    async def _finish_action(self, awaitable: Awaitable[ActionResult]) -> None:
        result = await awaitable
        self.show_action_message(result.message)
        if result.succeeded and result.refresh_after:
            self.request_refresh()

    def _cancel_job_after_confirm(
        self,
        job: JobStatusInfo,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(self.action_service.cancel_job(job.job_id))

    def _restart_workers_after_dialog(self, timeout: float | None) -> None:
        if timeout is not None:
            self._run_action(
                self.action_service.restart_workers(restart_timeout=timeout)
            )

    def _refresh_catalog_after_confirm(
        self,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(self.action_service.refresh_plugin_catalog())

    def _add_plugin_repo_after_dialog(self, form: PluginRepoForm | None) -> None:
        if form is not None:
            self._run_action(
                self.action_service.create_plugin_repo(
                    source=form.source,
                    repo_id=form.repo_id,
                )
            )

    def _toggle_plugin_repo_after_confirm(
        self,
        repo: PluginRepoResponse,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(
                self.action_service.set_plugin_repo_enabled(
                    repo_id=repo.id,
                    enabled=not repo.enabled,
                )
            )

    def _delete_plugin_repo_after_confirm(
        self,
        repo: PluginRepoResponse,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(self.action_service.delete_plugin_repo(repo.id))

    def _sync_plugin_repo_after_confirm(
        self,
        repo: PluginRepoResponse,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(self.action_service.sync_plugin_repo(repo.id))

    def _assign_route_after_dialog(self, form: RoutingForm | None) -> None:
        if form is not None:
            self._run_action(
                self.action_service.set_plugin_routing(
                    metric_name=form.metric_name,
                    queue=form.queue,
                )
            )

    def _delete_route_after_confirm(
        self,
        metric_name: str,
        *,
        confirmed: bool | None,
    ) -> None:
        if confirmed:
            self._run_action(self.action_service.delete_plugin_routing(metric_name))

    def _selected_job(self) -> JobStatusInfo | None:
        return self.query_one(JobsView).selected_job()

    def _selected_repo(self) -> PluginRepoResponse | None:
        return self.query_one(PluginsView).selected_repo()

    def _selected_route(self) -> tuple[str, str] | None:
        return self.query_one(PluginsView).selected_route()

    def _allowed_queues(self) -> list[str]:
        if self.state.snapshot.plugin_routing is not None:
            return self.state.snapshot.plugin_routing.allowed_queues
        if self.state.snapshot.admin_status is not None:
            return self.state.snapshot.admin_status.allowed_queues
        return []

    def _catalog_tab_is_active(self) -> bool:
        return self.query_one(TabbedContent).active == CATALOG_TAB_ID

    def _require_catalog_tab(self) -> bool:
        if self._catalog_tab_is_active():
            return True
        self.show_action_message(CATALOG_TAB_REQUIRED_MESSAGE)
        return False

    def _tab_strip_is_focused(self) -> bool:
        return isinstance(self.screen.focused, Tabs)

    def _table_is_focused(self) -> bool:
        return isinstance(self.screen.focused, DataTable)

    def _active_tab_focus_target(self) -> DataTable[object] | None:
        for table in self._active_tab_tables():
            return table
        return None

    def _active_tab_tables(self) -> list[DataTable[object]]:
        pane = self.query_one(TabbedContent).active_pane
        if pane is None:
            return []
        return list(pane.query(DataTable))

    def _focus_relative_table(self, direction: int) -> None:
        tables = self._active_tab_tables()
        focused = self.screen.focused
        for index, table in enumerate(tables):
            if table is focused:
                tables[(index + direction) % len(tables)].focus()
                return


def _loading_snapshot(snapshot: TuiSnapshot) -> TuiSnapshot:
    phase: SnapshotPhase = "loading"
    return TuiSnapshot(
        phase=phase,
        readiness=snapshot.readiness,
        admin_status=snapshot.admin_status,
        config_summary=snapshot.config_summary,
        catalog=snapshot.catalog,
        workers=snapshot.workers,
        queues=snapshot.queues,
        jobs=snapshot.jobs,
        plugin_repos=snapshot.plugin_repos,
        plugin_routing=snapshot.plugin_routing,
        last_updated=snapshot.last_updated,
    )
