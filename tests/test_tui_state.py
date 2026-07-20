from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lyra.api import DownloadError
from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    ConfigSummaryResponse,
    DatabaseHealth,
    JobListResponse,
    JobStatusInfo,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    QueuesResponse,
    QueueSummary,
    ReadinessResponse,
    RedisHealth,
    WorkerConfigSummary,
    WorkersResponse,
    WorkerSummary,
)
from lyra.tui import LyraTuiApp, TuiConfig
from lyra.tui.state import LyraTuiState, TuiSnapshot, refresh_snapshot
from lyra.tui.widgets import ConnectionStatus

if TYPE_CHECKING:
    from lyra.tui.client import LyraTuiReadClient


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_readiness(self) -> ReadinessResponse:
        self.calls.append("get_readiness")
        return _health_response()

    async def get_admin_status(self) -> AdminStatusResponse:
        self.calls.append("get_admin_status")
        return _admin_status_response()

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        self.calls.append("get_admin_config_summary")
        return _config_summary_response()

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        self.calls.append("get_admin_catalog")
        return _catalog_summary_response()

    async def get_admin_workers(self) -> WorkersResponse:
        self.calls.append("get_admin_workers")
        return _workers_response()

    async def get_admin_queues(self) -> QueuesResponse:
        self.calls.append("get_admin_queues")
        return _queues_response()

    async def list_admin_jobs(self) -> JobListResponse:
        self.calls.append("list_admin_jobs")
        return _job_list_response()

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        self.calls.append("list_plugin_repos")
        return _plugin_repo_list_response()

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        self.calls.append("list_plugin_routing")
        return _plugin_routing_response()


class AdminAuthFailureClient(FakeClient):
    async def get_admin_status(self) -> AdminStatusResponse:
        self.calls.append("get_admin_status")
        message = "Failed to fetch admin status. HTTP 403: forbidden"
        raise DownloadError(message)


class RecoveringClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_health = True

    async def get_readiness(self) -> ReadinessResponse:
        self.calls.append("get_readiness")
        if self.fail_next_health:
            self.fail_next_health = False
            message = "Health request error: connection refused"
            raise DownloadError(message)
        return _health_response()


class BlockingHealthClient(FakeClient):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__()
        self.release = release

    async def get_readiness(self) -> ReadinessResponse:
        self.calls.append("get_readiness")
        await self.release.wait()
        return _health_response()


def test_successful_snapshot_refresh_fetches_admin_data() -> None:
    client = FakeClient()

    snapshot = asyncio.run(refresh_snapshot(client, has_admin_key=True))

    assert snapshot.phase == "ready"
    assert snapshot.readiness is not None
    assert snapshot.admin_status is not None
    assert snapshot.config_summary is not None
    assert snapshot.catalog is not None
    assert snapshot.workers is not None
    assert snapshot.queues is not None
    assert snapshot.jobs is not None
    assert snapshot.plugin_repos is not None
    assert snapshot.plugin_routing is not None
    assert not snapshot.errors
    assert client.calls == [
        "get_readiness",
        "get_admin_status",
        "get_admin_config_summary",
        "get_admin_catalog",
        "get_admin_workers",
        "get_admin_queues",
        "list_admin_jobs",
        "list_plugin_repos",
        "list_plugin_routing",
    ]


def test_missing_admin_key_fetches_only_public_health() -> None:
    client = FakeClient()

    snapshot = asyncio.run(refresh_snapshot(client, has_admin_key=False))

    assert snapshot.phase == "auth-required"
    assert snapshot.readiness is not None
    assert snapshot.admin_status is None
    assert snapshot.errors[0].kind == "auth"
    assert client.calls == ["get_readiness"]


def test_admin_auth_failure_keeps_public_health() -> None:
    client = AdminAuthFailureClient()

    snapshot = asyncio.run(refresh_snapshot(client, has_admin_key=True))

    assert snapshot.phase == "auth-required"
    assert snapshot.readiness is not None
    assert snapshot.admin_status is None
    assert snapshot.errors[0].kind == "auth"
    assert client.calls == ["get_readiness", "get_admin_status"]


def test_failed_refresh_does_not_poison_later_success() -> None:
    client = RecoveringClient()
    state = LyraTuiState(client, has_admin_key=True)

    failed = asyncio.run(state.refresh())
    recovered = asyncio.run(state.refresh())

    assert failed.phase == "error"
    assert failed.errors[0].kind == "connection"
    assert recovered.phase == "ready"
    assert recovered.admin_status is not None
    assert not recovered.errors


def test_app_starts_with_waiting_status() -> None:
    async def run() -> None:
        app = LyraTuiApp(TuiConfig(), poll_on_mount=False)
        async with app.run_test():
            status = app.query_one(ConnectionStatus)
            assert "Waiting for first refresh" in status.message

    asyncio.run(run())


def test_app_shows_injected_state_in_status_area() -> None:
    async def run() -> None:
        state = LyraTuiState(FakeClient(), has_admin_key=True)
        state.snapshot = TuiSnapshot(
            phase="ready",
            readiness=_health_response(),
            admin_status=_admin_status_response(),
        )
        app = LyraTuiApp(
            TuiConfig(admin_api_key="secret"),
            state=state,
            poll_on_mount=False,
        )
        async with app.run_test():
            status = app.query_one(ConnectionStatus)
            assert "API ready v0.1.0" in status.message
            assert "metrics 1" in status.message

    asyncio.run(run())


def test_app_skips_overlapping_refresh_requests() -> None:
    async def run() -> None:
        release = asyncio.Event()
        client = BlockingHealthClient(release)
        state = LyraTuiState(client, has_admin_key=True)
        app = LyraTuiApp(
            TuiConfig(admin_api_key="secret"),
            state=state,
            poll_on_mount=False,
        )

        async with app.run_test() as pilot:
            app.request_refresh()
            await pilot.pause()
            app.request_refresh()
            await pilot.pause()

            assert client.calls.count("get_readiness") == 1

            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

            status = app.query_one(ConnectionStatus)
            assert "API ready v0.1.0" in status.message
            assert client.calls.count("get_readiness") == 1

    asyncio.run(run())


def _health_response() -> ReadinessResponse:
    return ReadinessResponse(
        status="ready",
        api_version="0.1.0",
        redis=RedisHealth(status="ok"),
        database=DatabaseHealth(status="ok"),
    )


def _admin_status_response() -> AdminStatusResponse:
    return AdminStatusResponse(
        api_version="0.1.0",
        redis=RedisHealth(status="ok"),
        metric_count=1,
        allowed_queues=["interactive"],
        default_queue="interactive",
        configured_worker_count=1,
        job_store_ttl_seconds=600,
        catalog_fingerprint="abc",
    )


def _config_summary_response() -> ConfigSummaryResponse:
    return ConfigSummaryResponse(
        api_host="0.0.0.0",
        api_port=5219,
        allowed_queues=["interactive"],
        default_queue="interactive",
        workers=[
            WorkerConfigSummary(
                name="interactive",
                queues=["interactive"],
                concurrency=1,
                install_dir="/lyra_data/plugins/runners/interactive",
                temp_dir="/lyra_data/cache/jobs/interactive",
            )
        ],
        job_store_ttl_seconds=600,
        plugin_catalog_dir="/lyra_data/plugins/catalog",
        plugin_state_path="/lyra_data/state/plugins.toml",
        plugin_runner_base_dir="/lyra_data/plugins/runners",
    )


def _catalog_summary_response() -> CatalogSummaryResponse:
    return CatalogSummaryResponse(
        metric_count=1,
        metric_names=["smoke_metric"],
        catalog_fingerprint="abc",
        plugin_sources=[],
        metric_queues={"smoke_metric": "interactive"},
    )


def _workers_response() -> WorkersResponse:
    return WorkersResponse(
        inspect_available=True,
        workers=[
            WorkerSummary(
                name="interactive",
                configured=True,
                observed=True,
                status="online",
                queues=["interactive"],
                active_count=0,
                reserved_count=0,
                scheduled_count=0,
            )
        ],
    )


def _queues_response() -> QueuesResponse:
    return QueuesResponse(
        allowed_queues=["interactive"],
        default_queue="interactive",
        queues=[
            QueueSummary(
                name="interactive",
                is_default=True,
                assigned_metric_count=1,
                configured_workers=["interactive"],
                observed_workers=["interactive"],
                pending_depth=None,
                pending_depth_unknown=True,
            )
        ],
    )


def _job_list_response() -> JobListResponse:
    return JobListResponse(
        jobs=[
            JobStatusInfo(
                job_id="job-1",
                status="started",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                metric="smoke_metric",
            )
        ],
    )


def _plugin_repo_list_response() -> PluginRepoListResponse:
    return PluginRepoListResponse(
        repos=[
            PluginRepoResponse(
                id="smoke",
                source="dir:///plugins/smoke",
                ref=None,
                enabled=True,
            )
        ],
    )


def _plugin_routing_response() -> PluginRoutingResponse:
    return PluginRoutingResponse(
        metric_queues={"smoke_metric": "interactive"},
        allowed_queues=["interactive"],
        default_queue="interactive",
    )


def _client_type_check(_: LyraTuiReadClient) -> None:
    return None


_client_type_check(FakeClient())
