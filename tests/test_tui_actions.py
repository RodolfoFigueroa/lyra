from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from lyra.api import DownloadError
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    CreatePluginRepoResponse,
    DatabaseHealth,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    JobCancelResponse,
    JobListResponse,
    JobStatusInfo,
    MetricQueueAssignmentResponse,
    PluginCatalogRefreshResponse,
    PluginCatalogRefreshStatus,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    QueuesResponse,
    ReadinessResponse,
    RedisHealth,
    SyncPluginRepoResponse,
    UpdatePluginRepoResponse,
    WorkerRestartResponse,
    WorkersResponse,
)
from lyra.tui import LyraTuiApp, TuiConfig
from lyra.tui.actions import ActionResult, ActionService
from lyra.tui.app import CATALOG_TAB_REQUIRED_MESSAGE
from lyra.tui.state import LyraTuiState, TuiSnapshot
from lyra.tui.widgets import (
    ActionMessage,
    ConfirmDialog,
    PluginRepoDialog,
    RestartWorkersDialog,
)
from textual.widgets import Button, DataTable, Input, TabbedContent, Tabs

if TYPE_CHECKING:
    from lyra.tui.client import LyraTuiClient


class _FooterKeyLike(Protocol):
    action: str


class FakeActionClient:
    def __init__(self) -> None:
        self.cancelled_jobs: list[str] = []
        self.restart_timeouts: list[float] = []
        self.created_repos: list[tuple[str, str | None]] = []
        self.updated_repos: list[tuple[str, bool | None]] = []
        self.deleted_repos: list[str] = []
        self.synced_repos: list[str] = []
        self.catalog_refreshes = 0
        self.set_routes: list[tuple[str, str]] = []
        self.deleted_routes: list[str] = []

    @staticmethod
    async def get_readiness() -> ReadinessResponse:
        return ReadinessResponse(
            status="ready",
            api_version="0.1.0",
            redis=RedisHealth(status="ok"),
            database=DatabaseHealth(status="ok"),
        )

    @staticmethod
    async def get_admin_status() -> AdminStatusResponse:
        return _admin_status_response()

    @staticmethod
    async def get_admin_config_summary() -> object:
        message = "config summary not needed"
        raise DownloadError(message)

    @staticmethod
    async def get_admin_catalog() -> CatalogSummaryResponse:
        return CatalogSummaryResponse(
            metric_count=1,
            metric_names=["metric_a"],
            catalog_fingerprint="abc",
            plugin_sources=[],
            metric_queues={"metric_a": "interactive"},
        )

    @staticmethod
    async def get_admin_workers() -> WorkersResponse:
        return WorkersResponse(inspect_available=True, workers=[])

    @staticmethod
    async def get_admin_queues() -> QueuesResponse:
        return QueuesResponse(
            allowed_queues=["interactive"],
            default_queue="interactive",
            queues=[],
        )

    @staticmethod
    async def list_admin_jobs() -> JobListResponse:
        return JobListResponse(jobs=_jobs())

    @staticmethod
    async def list_plugin_repos() -> PluginRepoListResponse:
        return PluginRepoListResponse(repos=[_repo()])

    @staticmethod
    async def list_plugin_routing() -> PluginRoutingResponse:
        return PluginRoutingResponse(
            metric_queues={"metric_a": "interactive"},
            allowed_queues=["interactive"],
            default_queue="interactive",
        )

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        self.cancelled_jobs.append(job_id)
        return JobCancelResponse(
            job_id=job_id,
            status="cancelled",
            cancellation_requested=True,
            revoke_requested=True,
        )

    async def restart_workers(
        self,
        *,
        restart_timeout: float = 30.0,
    ) -> WorkerRestartResponse:
        self.restart_timeouts.append(restart_timeout)
        return WorkerRestartResponse(
            requested=True,
            timeout=restart_timeout,
            message="Worker restart requested.",
        )

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        del enabled
        self.created_repos.append((source, repo_id))
        return CreatePluginRepoResponse(
            repo=PluginRepoResponse(
                id=repo_id or "new",
                source=source,
                ref=None,
                enabled=True,
            ),
            catalog_refresh=_catalog_refresh_status(),
        )

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        del source
        self.updated_repos.append((repo_id, enabled))
        return UpdatePluginRepoResponse(
            repo=PluginRepoResponse(
                id=repo_id,
                source="dir:///plugins/smoke",
                ref=None,
                enabled=bool(enabled),
            ),
            catalog_refresh=_catalog_refresh_status(),
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        self.deleted_repos.append(repo_id)
        return DeletePluginRepoResponse(
            deleted=True,
            repo_id=repo_id,
            removed_metric_queues=["metric_a"],
            catalog_refresh=_catalog_refresh_status(),
        )

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        self.synced_repos.append(repo_id)
        return SyncPluginRepoResponse(
            repo_id=repo_id,
            changed=True,
            display_name="Smoke",
            catalog_refresh=_catalog_refresh_status(),
        )

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        self.catalog_refreshes += 1
        return PluginCatalogRefreshResponse(
            updated_plugins=["smoke"],
            catalog_changed=True,
            previous_catalog_fingerprint="old",
            catalog_fingerprint="new",
            assigned_metric_queues=[],
            removed_metric_queues=[],
            workers_restarted=False,
            workers_restart_recommended=True,
            message="Catalog refreshed.",
        )

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        self.set_routes.append((metric_name, queue))
        return MetricQueueAssignmentResponse(metric_name=metric_name, queue=queue)

    async def delete_plugin_routing(
        self, metric_name: str
    ) -> DeleteMetricQueueResponse:
        self.deleted_routes.append(metric_name)
        return DeleteMetricQueueResponse(deleted=True, metric_name=metric_name)


class FailingCancelClient(FakeActionClient):
    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        assert isinstance(self, FailingCancelClient)
        del job_id
        message = "Failed to cancel admin job. HTTP 409: terminal"
        raise DownloadError(message)


class CatalogRefreshFailureClient(FakeActionClient):
    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        del enabled
        self.created_repos.append((source, repo_id))
        return CreatePluginRepoResponse(
            repo=PluginRepoResponse(
                id=repo_id or "new",
                source=source,
                ref=None,
                enabled=True,
            ),
            catalog_refresh=_catalog_refresh_status(
                refreshed=False,
                error="catalog sync failed",
            ),
        )


def test_action_service_formats_successes() -> None:
    client = FakeActionClient()
    service = ActionService(cast("LyraTuiClient", client))

    results = asyncio.run(_exercise_successes(service))

    assert all(result.succeeded for result in results)
    assert client.cancelled_jobs == ["job-1"]
    assert client.restart_timeouts == [5.0]
    assert client.created_repos == [("dir:///plugins/new", "new")]
    assert client.updated_repos == [("smoke", False)]
    assert client.deleted_repos == ["smoke"]
    assert client.synced_repos == ["smoke"]
    assert client.catalog_refreshes == 1
    assert client.set_routes == [("metric_a", "interactive")]
    assert client.deleted_routes == ["metric_a"]


def test_action_service_formats_common_failure() -> None:
    service = ActionService(cast("LyraTuiClient", FailingCancelClient()))

    result = asyncio.run(service.cancel_job("job-1"))

    assert not result.succeeded
    assert "HTTP 409" in result.message


def test_action_service_reports_repo_refresh_failure() -> None:
    client = CatalogRefreshFailureClient()
    service = ActionService(cast("LyraTuiClient", client))

    result = asyncio.run(
        service.create_plugin_repo(source="dir:///plugins/new", repo_id="new")
    )

    assert result.succeeded
    assert result.refresh_after
    assert result.message == (
        "Added plugin repo new. Catalog refresh failed: catalog sync failed"
    )


def test_cancel_confirmation_declined_does_not_call_client() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            app.action_cancel_job()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmDialog)
            app.screen.action_cancel()
            await pilot.pause()
            assert client.cancelled_jobs == []

    asyncio.run(run())


def test_restart_confirmation_calls_client_after_acceptance() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            app.action_restart_workers()
            await pilot.pause()
            assert isinstance(app.screen, RestartWorkersDialog)
            app.screen.query_one("#timeout", Input).value = "7"
            app.screen.action_submit()
            await pilot.pause()
            assert client.restart_timeouts == [7.0]

    asyncio.run(run())


def test_dialogs_are_centered_on_screen() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_restart_workers()
            await pilot.pause()
            assert isinstance(app.screen, RestartWorkersDialog)

            dialog = app.screen.query_one(".dialog")
            dialog_region = dialog.region
            screen_size = app.screen.size
            dialog_center_x = dialog_region.x + dialog_region.width / 2
            dialog_center_y = dialog_region.y + dialog_region.height / 2

            assert abs(dialog_center_x - screen_size.width / 2) <= 2
            assert abs(dialog_center_y - screen_size.height / 2) <= 2

    asyncio.run(run())


def test_plugin_add_form_requires_source() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)
            app.screen.action_submit()
            await pilot.pause()
            assert app.screen.error_message == "Source is required."
            assert client.created_repos == []

    asyncio.run(run())


def test_plugin_add_form_has_keyboard_focus_order() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)

            source = app.screen.query_one("#source", Input)
            repo_id = app.screen.query_one("#repo-id", Input)
            cancel = app.screen.query_one("#cancel", Button)
            submit = app.screen.query_one("#submit", Button)

            assert app.screen.focused is source
            await pilot.press("down")
            assert app.screen.focused is repo_id
            await pilot.press("up")
            assert app.screen.focused is source
            await pilot.press("down", "down")
            assert app.screen.focused is cancel
            await pilot.press("right")
            assert app.screen.focused is submit
            await pilot.press("up")
            assert app.screen.focused is repo_id
            await pilot.press("tab")
            assert app.screen.focused is cancel
            await pilot.press("right")
            assert app.screen.focused is submit
            await pilot.press("left")
            assert app.screen.focused is cancel
            await pilot.press("tab")
            assert app.screen.focused is submit
            await pilot.press("shift+tab")
            assert app.screen.focused is cancel

    asyncio.run(run())


def test_plugin_add_form_left_and_right_edit_input_cursor() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)

            source = app.screen.query_one("#source", Input)
            source.value = "dir:///plugins/new"
            source.cursor_position = len(source.value)

            await pilot.press("left")
            assert app.screen.focused is source
            assert source.cursor_position == len(source.value) - 1
            await pilot.press("right")
            assert app.screen.focused is source
            assert source.cursor_position == len(source.value)

    asyncio.run(run())


def test_plugin_add_form_cancel_button_is_keyboard_activated() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)

            await pilot.press("tab", "tab")
            assert app.screen.focused is app.screen.query_one("#cancel", Button)
            await pilot.press("enter")
            await pilot.pause()

            assert not isinstance(app.screen, PluginRepoDialog)
            assert client.created_repos == []

    asyncio.run(run())


def test_plugin_add_form_submit_button_is_keyboard_activated() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)

            app.screen.query_one("#source", Input).value = "dir:///plugins/new"
            app.screen.query_one("#repo-id", Input).value = "new"
            await pilot.press("tab", "tab", "tab")
            assert app.screen.focused is app.screen.query_one("#submit", Button)
            await pilot.press("enter")
            await pilot.pause()

            assert client.created_repos == [("dir:///plugins/new", "new")]

    asyncio.run(run())


def test_catalog_actions_are_disabled_outside_catalog_tab() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            assert app.query_one(TabbedContent).active == "dashboard"

            assert app.check_action("refresh_catalog", ()) is False
            assert app.check_action("add_plugin_repo", ()) is False
            assert app.check_action("toggle_plugin_repo", ()) is False
            assert app.check_action("delete_plugin_repo", ()) is False
            assert app.check_action("sync_plugin_repo", ()) is False
            assert app.check_action("assign_route", ()) is False
            assert app.check_action("delete_route", ()) is False

            await pilot.press("d")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmDialog)

            app.action_delete_plugin_repo()
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmDialog)
            assert app.query_one(ActionMessage).message == CATALOG_TAB_REQUIRED_MESSAGE
            assert client.deleted_repos == []

    asyncio.run(run())


def test_catalog_actions_are_enabled_on_catalog_tab() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test():
            _activate_catalog(app)

            assert app.check_action("refresh_catalog", ()) is True
            assert app.check_action("add_plugin_repo", ()) is True
            assert app.check_action("toggle_plugin_repo", ()) is True
            assert app.check_action("delete_plugin_repo", ()) is True
            assert app.check_action("sync_plugin_repo", ()) is True
            assert app.check_action("assign_route", ()) is True
            assert app.check_action("delete_route", ()) is True

    asyncio.run(run())


def test_footer_keeps_catalog_actions_out_of_global_commands() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            tabs = app.query_one(Tabs)
            tabs.focus()
            await pilot.pause()
            assert "add_plugin_repo" not in _footer_actions(app)

            _activate_catalog(app)
            await pilot.pause()
            await pilot.pause()
            assert app.check_action("add_plugin_repo", ()) is True
            assert app.check_action("delete_plugin_repo", ()) is True
            assert "add_plugin_repo" not in _footer_actions(app)
            assert "delete_plugin_repo" not in _footer_actions(app)

            app.query_one(TabbedContent).active = "jobs"
            await pilot.pause()
            await pilot.pause()
            assert "add_plugin_repo" not in _footer_actions(app)
            assert "delete_plugin_repo" not in _footer_actions(app)

    asyncio.run(run())


def test_enter_on_tab_strip_focuses_selected_section() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            tabs = app.query_one(Tabs)
            tabs.focus()
            await pilot.pause()
            assert app.screen.focused is tabs
            assert app.check_action("focus_active_tab_content", ()) is True

            await pilot.press("right")
            await pilot.pause()
            assert app.query_one(TabbedContent).active == "jobs"
            assert app.screen.focused is tabs

            await pilot.press("enter")
            await pilot.pause()

            assert app.screen.focused is app.query_one("#jobs-table", DataTable)
            assert app.check_action("focus_active_tab_content", ()) is False
            assert app.check_action("focus_tab_strip", ()) is True

            await pilot.press("escape")
            await pilot.pause()

            assert app.screen.focused is tabs
            assert app.query_one(TabbedContent).active == "jobs"
            assert app.check_action("focus_tab_strip", ()) is False

    asyncio.run(run())


def test_tab_switches_between_tables_in_multitable_section() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            _activate_catalog(app)
            await pilot.pause()

            repos = app.query_one("#plugins-table", DataTable)
            routing = app.query_one("#routing-table", DataTable)
            repos.focus()
            await pilot.pause()

            assert app.screen.focused is repos
            assert app.check_action("focus_next_table", ()) is True
            assert app.check_action("focus_previous_table", ()) is True

            await pilot.press("tab")
            await pilot.pause()
            assert app.screen.focused is routing

            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.screen.focused is repos

            app.query_one(TabbedContent).active = "jobs"
            app.query_one("#jobs-table", DataTable).focus()
            await pilot.pause()
            assert app.check_action("focus_next_table", ()) is False
            assert app.check_action("focus_previous_table", ()) is False

    asyncio.run(run())


def test_cancel_without_selected_job_shows_message() -> None:
    async def run() -> None:
        client = FakeActionClient()
        state = LyraTuiState(cast("LyraTuiClient", client), has_admin_key=True)
        state.snapshot = _snapshot(jobs=[])
        app = LyraTuiApp(
            TuiConfig(admin_api_key="secret"),
            state=state,
            poll_on_mount=False,
        )
        async with app.run_test():
            app.action_cancel_job()
            assert app.query_one(ActionMessage).message == "No job selected."
            assert client.cancelled_jobs == []

    asyncio.run(run())


async def _exercise_successes(service: ActionService) -> list[ActionResult]:
    return [
        await service.cancel_job("job-1"),
        await service.restart_workers(restart_timeout=5.0),
        await service.create_plugin_repo(
            source="dir:///plugins/new",
            repo_id="new",
        ),
        await service.set_plugin_repo_enabled(repo_id="smoke", enabled=False),
        await service.delete_plugin_repo("smoke"),
        await service.sync_plugin_repo("smoke"),
        await service.refresh_plugin_catalog(),
        await service.set_plugin_routing(
            metric_name="metric_a",
            queue="interactive",
        ),
        await service.delete_plugin_routing("metric_a"),
    ]


def _app_with_client(client: FakeActionClient) -> LyraTuiApp:
    state = LyraTuiState(cast("LyraTuiClient", client), has_admin_key=True)
    state.snapshot = _snapshot(jobs=_jobs())
    return LyraTuiApp(
        TuiConfig(admin_api_key="secret"),
        state=state,
        poll_on_mount=False,
    )


def _activate_catalog(app: LyraTuiApp) -> None:
    app.query_one(TabbedContent).active = "catalog"


def _footer_actions(app: LyraTuiApp) -> set[str]:
    return {
        cast("_FooterKeyLike", footer_key).action
        for footer_key in app.query("FooterKey")
    }


def _snapshot(*, jobs: list[JobStatusInfo]) -> TuiSnapshot:
    return TuiSnapshot(
        phase="ready",
        readiness=ReadinessResponse(
            status="ready",
            api_version="0.1.0",
            redis=RedisHealth(status="ok"),
            database=DatabaseHealth(status="ok"),
        ),
        admin_status=_admin_status_response(),
        catalog=CatalogSummaryResponse(
            metric_count=1,
            metric_names=["metric_a"],
            catalog_fingerprint="abc",
            plugin_sources=[],
            metric_queues={"metric_a": "interactive"},
        ),
        workers=WorkersResponse(inspect_available=True, workers=[]),
        queues=QueuesResponse(
            allowed_queues=["interactive"],
            default_queue="interactive",
            queues=[],
        ),
        jobs=JobListResponse(jobs=jobs),
        plugin_repos=PluginRepoListResponse(repos=[_repo()]),
        plugin_routing=PluginRoutingResponse(
            metric_queues={"metric_a": "interactive"},
            allowed_queues=["interactive"],
            default_queue="interactive",
        ),
    )


def _jobs() -> list[JobStatusInfo]:
    return [
        JobStatusInfo(
            job_id="job-1",
            status="running",
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            metric="metric_a",
        )
    ]


def _repo() -> PluginRepoResponse:
    return PluginRepoResponse(
        id="smoke",
        source="dir:///plugins/smoke",
        ref=None,
        enabled=True,
    )


def _catalog_refresh_status(
    *,
    refreshed: bool = True,
    error: str | None = None,
) -> PluginCatalogRefreshStatus:
    return PluginCatalogRefreshStatus(
        refreshed=refreshed,
        error=error,
        catalog_changed=(False if refreshed else None),
        previous_catalog_fingerprint=("same" if refreshed else None),
        catalog_fingerprint=("same" if refreshed else None),
        assigned_metric_queues=[],
        removed_metric_queues=[],
        workers_restart_recommended=False,
    )


def _admin_status_response() -> AdminStatusResponse:
    return AdminStatusResponse(
        api_version="0.1.0",
        redis=RedisHealth(status="ok"),
        metric_count=1,
        allowed_queues=["interactive"],
        default_queue="interactive",
        configured_worker_count=0,
        job_store_ttl_seconds=600,
        catalog_fingerprint="abc",
    )
