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
from textual.widgets import Footer, Header, TabbedContent, TabPane

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from typing import ClassVar

    from lyra.sdk.models import JobStatusInfo, PluginRepoResponse
    from lyra.tui.client import LyraTuiClient
    from lyra.tui.config import TuiConfig
    from lyra.tui.state import SnapshotPhase
    from textual.worker import Worker


class LyraTuiApp(App[None]):
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

    .panel-summary {
        height: auto;
        padding: 0 1;
    }

    .panel-message {
        height: auto;
        padding: 0 1;
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
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("r", "refresh", "Refresh"),
        Binding("c", "cancel_job", "Cancel Job"),
        Binding("w", "restart_workers", "Restart Workers"),
        Binding("p", "refresh_catalog", "Refresh Catalog"),
        Binding("a", "add_plugin_repo", "Add Repo"),
        Binding("e", "toggle_plugin_repo", "Enable/Disable Repo"),
        Binding("d", "delete_plugin_repo", "Delete Repo"),
        Binding("s", "sync_plugin_repo", "Sync Repo"),
        Binding("m", "assign_route", "Assign Route"),
        Binding("x", "delete_route", "Delete Route"),
        Binding("q", "quit_app", "Quit", priority=True),
    ]

    def __init__(
        self,
        config: TuiConfig,
        *,
        state: LyraTuiState | None = None,
        poll_on_mount: bool = True,
    ) -> None:
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
        if not self.poll_on_mount:
            return
        self.request_refresh()
        self._refresh_timer = self.set_interval(
            self.config.refresh_interval,
            self.request_refresh,
            name="refresh",
        )

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self.workers.cancel_group(self, "refresh")
        self.workers.cancel_group(self, "action")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        result: bool | None = None
        try:
            if action == "quit_app":
                result = True
            elif action == "cancel_job":
                job = self._selected_job()
                result = job is not None and is_active_job_status(job.status)
            elif action in {
                "restart_workers",
                "refresh_catalog",
                "add_plugin_repo",
                "assign_route",
            }:
                result = self.config.has_admin_key
            elif action in {
                "toggle_plugin_repo",
                "delete_plugin_repo",
                "sync_plugin_repo",
            }:
                result = self.config.has_admin_key and self._selected_repo() is not None
            elif action == "delete_route":
                result = (
                    self.config.has_admin_key and self._selected_route() is not None
                )
        except Exception:  # noqa: BLE001
            result = None
        return result

    def request_refresh(self) -> None:
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
        self.request_refresh()

    def action_quit_app(self) -> None:
        self.exit()

    def action_cancel_job(self) -> None:
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
        self.push_screen(
            RestartWorkersDialog(timeout=30.0),
            callback=self._restart_workers_after_dialog,
        )

    def action_refresh_catalog(self) -> None:
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
        self.push_screen(
            PluginRepoDialog(),
            callback=self._add_plugin_repo_after_dialog,
        )

    def action_toggle_plugin_repo(self) -> None:
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
        snapshot = await self.state.refresh()
        self.show_snapshot(snapshot)

    def show_snapshot(self, snapshot: TuiSnapshot) -> None:
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


def _loading_snapshot(snapshot: TuiSnapshot) -> TuiSnapshot:
    phase: SnapshotPhase = "loading"
    return TuiSnapshot(
        phase=phase,
        health=snapshot.health,
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
