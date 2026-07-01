from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from lyra.sdk.models import (
    AdminStatusResponse,
    CatalogSummaryResponse,
    HealthResponse,
    JobListResponse,
    JobStatusInfo,
    PluginRepoListResponse,
    PluginRepoResponse,
    PluginRoutingResponse,
    QueuesResponse,
    QueueSummary,
    RedisHealth,
    WorkersResponse,
    WorkerSummary,
)
from lyra.tui import LyraTuiApp, TuiConfig
from lyra.tui.screens import (
    dashboard_rows,
    job_row,
    queue_depth_label,
    queue_row,
    worker_row,
)
from lyra.tui.state import LyraTuiState, TuiSnapshot
from lyra.tui.widgets import EmptyState
from textual.widgets import DataTable

if TYPE_CHECKING:
    from lyra.tui.client import LyraTuiClient


class NoopClient:
    pass


def test_dashboard_rows_keep_public_health_without_admin() -> None:
    snapshot = TuiSnapshot(
        phase="auth-required",
        health=_health_response(),
    )

    rows = dict(dashboard_rows(snapshot))

    assert rows["API status"] == "ok"
    assert rows["Redis status"] == "ok"
    assert rows["Admin"] == "locked"


def test_job_row_uses_text_status_prefix() -> None:
    row = job_row(
        JobStatusInfo(
            job_id="job-1234567890",
            status="started",
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            metric="very_long_metric_name",
        )
    )

    assert row[1] == "RUN started"
    assert row[3] == "2026-01-01T00:00:00Z"


def test_worker_row_shows_offline_state_and_unknown_counts() -> None:
    row = worker_row(
        WorkerSummary(
            name="batch",
            configured=True,
            observed=False,
            status="offline",
            queues=["batch"],
            active_count=None,
            reserved_count=None,
            scheduled_count=None,
        )
    )

    assert row[1] == "OFF offline"
    assert row[5] == "unknown"


def test_queue_row_does_not_turn_unknown_depth_into_zero() -> None:
    queue = QueueSummary(
        name="interactive",
        is_default=True,
        assigned_metric_count=1,
        configured_workers=["interactive"],
        observed_workers=[],
        pending_depth=None,
        pending_depth_unknown=True,
    )

    assert queue_depth_label(queue) == "unknown"
    assert queue_row(queue)[5] == "unknown"


def test_app_renders_dashboard_jobs_workers_and_queues() -> None:
    async def run() -> None:
        app = _app_with_snapshot(_ready_snapshot())
        async with app.run_test():
            dashboard = app.query_one("#dashboard-table", DataTable)
            jobs = app.query_one("#jobs-table", DataTable)
            workers = app.query_one("#workers-table", DataTable)
            queues = app.query_one("#queues-table", DataTable)

            assert dashboard.row_count > 0
            assert jobs.row_count == 3
            assert workers.row_count == 3
            assert queues.row_count == 1

    asyncio.run(run())


def test_jobs_empty_state_message() -> None:
    async def run() -> None:
        snapshot = _ready_snapshot()
        snapshot = TuiSnapshot(
            phase=snapshot.phase,
            health=snapshot.health,
            admin_status=snapshot.admin_status,
            catalog=snapshot.catalog,
            workers=snapshot.workers,
            queues=snapshot.queues,
            jobs=JobListResponse(jobs=[]),
            plugin_repos=snapshot.plugin_repos,
            plugin_routing=snapshot.plugin_routing,
            last_updated=snapshot.last_updated,
        )
        app = _app_with_snapshot(snapshot)
        async with app.run_test():
            empty = app.query_one("#jobs-empty", EmptyState)
            jobs = app.query_one("#jobs-table", DataTable)
            assert empty.message == "No recent jobs."
            assert jobs.row_count == 0

    asyncio.run(run())


def test_queues_view_renders_unknown_depth_distinctly() -> None:
    async def run() -> None:
        app = _app_with_snapshot(_ready_snapshot())
        async with app.run_test():
            queues = app.query_one("#queues-table", DataTable)
            row = queues.get_row("interactive")
            assert row[5] == "unknown"

    asyncio.run(run())


def _app_with_snapshot(snapshot: TuiSnapshot) -> LyraTuiApp:
    state = LyraTuiState(cast("LyraTuiClient", NoopClient()), has_admin_key=True)
    state.snapshot = snapshot
    return LyraTuiApp(
        TuiConfig(admin_api_key="secret"),
        state=state,
        poll_on_mount=False,
    )


def _ready_snapshot() -> TuiSnapshot:
    return TuiSnapshot(
        phase="ready",
        health=_health_response(),
        admin_status=_admin_status_response(),
        catalog=_catalog_summary_response(),
        workers=_workers_response(),
        queues=_queues_response(),
        jobs=_job_list_response(),
        plugin_repos=_plugin_repo_list_response(),
        plugin_routing=_plugin_routing_response(),
        last_updated=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _health_response() -> HealthResponse:
    return HealthResponse(
        status="ok",
        api_version="0.1.0",
        redis=RedisHealth(status="ok"),
    )


def _admin_status_response() -> AdminStatusResponse:
    return AdminStatusResponse(
        api_version="0.1.0",
        redis=RedisHealth(status="ok"),
        metric_count=2,
        allowed_queues=["interactive", "batch"],
        default_queue="interactive",
        configured_worker_count=3,
        job_store_ttl_seconds=600,
        catalog_fingerprint="abc",
    )


def _catalog_summary_response() -> CatalogSummaryResponse:
    return CatalogSummaryResponse(
        metric_count=2,
        metric_names=["metric_a", "metric_b"],
        catalog_fingerprint="abc",
        plugin_sources=[],
        metric_queues={"metric_a": "interactive", "metric_b": "batch"},
    )


def _workers_response() -> WorkersResponse:
    return WorkersResponse(
        inspect_available=False,
        workers=[
            WorkerSummary(
                name="interactive",
                configured=True,
                observed=True,
                status="online",
                queues=["interactive"],
                active_count=1,
                reserved_count=0,
                scheduled_count=0,
            ),
            WorkerSummary(
                name="batch",
                configured=True,
                observed=False,
                status="offline",
                queues=["batch"],
            ),
            WorkerSummary(
                name="mystery",
                configured=False,
                observed=False,
                status="unknown",
                queues=[],
            ),
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
                observed_workers=[],
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
                status="queued",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                metric="metric_a",
            ),
            JobStatusInfo(
                job_id="job-2",
                status="started",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                metric="metric_b",
            ),
            JobStatusInfo(
                job_id="job-3",
                status="failed",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                metric="metric_b",
                error={"message": "failed"},
            ),
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
        metric_queues={"metric_a": "interactive", "metric_b": "batch"},
        allowed_queues=["interactive", "batch"],
        default_queue="interactive",
    )
