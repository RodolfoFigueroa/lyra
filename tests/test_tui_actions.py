from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from lyra.api import DownloadError
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    DeleteMetricQueueResponse,
    DeletePluginRepoResponse,
    HealthResponse,
    JobCancelResponse,
    JobListResponse,
    JobStatusInfo,
    MetricQueueAssignmentResponse,
    PluginCatalogRefreshResponse,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    QueuesResponse,
    RedisHealth,
    SyncPluginRepoResponse,
    WorkerRestartResponse,
    WorkersResponse,
)
from lyra.tui import LyraTuiApp, TuiConfig
from lyra.tui.actions import ActionResult, ActionService
from lyra.tui.state import LyraTuiState, TuiSnapshot
from lyra.tui.widgets import (
    ActionMessage,
    ConfirmDialog,
    PluginRepoDialog,
    RestartWorkersDialog,
)
from textual.widgets import Input

if TYPE_CHECKING:
    from lyra.tui.client import LyraTuiClient


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

    async def get_health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            api_version="0.1.0",
            redis=RedisHealth(status="ok"),
        )

    async def get_admin_status(self) -> AdminStatusResponse:
        return _admin_status_response()

    async def get_admin_config_summary(self) -> object:
        message = "config summary not needed"
        raise DownloadError(message)

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        return CatalogSummaryResponse(
            metric_count=1,
            metric_names=["metric_a"],
            catalog_fingerprint="abc",
            plugin_sources=[],
            metric_queues={"metric_a": "interactive"},
        )

    async def get_admin_workers(self) -> WorkersResponse:
        return WorkersResponse(inspect_available=True, workers=[])

    async def get_admin_queues(self) -> QueuesResponse:
        return QueuesResponse(
            allowed_queues=["interactive"],
            default_queue="interactive",
            queues=[],
        )

    async def list_admin_jobs(self) -> JobListResponse:
        return JobListResponse(jobs=_jobs())

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        return PluginRepoListResponse(repos=[_repo()])

    async def list_plugin_routing(self) -> PluginRoutingResponse:
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
    ) -> PluginRepoResponse:
        del enabled
        self.created_repos.append((source, repo_id))
        return PluginRepoResponse(
            id=repo_id or "new",
            source=source,
            ref=None,
            enabled=True,
        )

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> PluginRepoResponse:
        del source
        self.updated_repos.append((repo_id, enabled))
        return PluginRepoResponse(
            id=repo_id,
            source="dir:///plugins/smoke",
            ref=None,
            enabled=bool(enabled),
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        self.deleted_repos.append(repo_id)
        return DeletePluginRepoResponse(deleted=True, repo_id=repo_id)

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        self.synced_repos.append(repo_id)
        return SyncPluginRepoResponse(
            repo_id=repo_id,
            changed=True,
            display_name="Smoke",
        )

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        self.catalog_refreshes += 1
        return PluginCatalogRefreshResponse(
            updated_plugins=["smoke"],
            catalog_changed=True,
            previous_catalog_fingerprint="old",
            catalog_fingerprint="new",
            assigned_metric_queues=[],
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
        del job_id
        message = "Failed to cancel admin job. HTTP 409: terminal"
        raise DownloadError(message)


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


def test_plugin_add_form_requires_source() -> None:
    async def run() -> None:
        client = FakeActionClient()
        app = _app_with_client(client)
        async with app.run_test() as pilot:
            app.action_add_plugin_repo()
            await pilot.pause()
            assert isinstance(app.screen, PluginRepoDialog)
            app.screen.action_submit()
            await pilot.pause()
            assert app.screen.error_message == "Source is required."
            assert client.created_repos == []

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


def _snapshot(*, jobs: list[JobStatusInfo]) -> TuiSnapshot:
    return TuiSnapshot(
        phase="ready",
        health=HealthResponse(
            status="ok",
            api_version="0.1.0",
            redis=RedisHealth(status="ok"),
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
            status="started",
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
