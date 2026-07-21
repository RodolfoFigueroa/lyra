import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar, NotRequired, Self, TypedDict, Unpack, cast

import pytest
import requests
from lyra.api import parse_result_ref
from lyra.api.client.async_ import AsyncLyraClient
from lyra.api.client.sync import LyraClient
from lyra.api.exceptions import DownloadError, ServiceUnavailableError
from lyra.sdk.models import FileJobResult, JobProgressEvent, TableJobResult
from lyra.sdk.types import JsonValue

from lyra_app.config import DEFAULT_API_HOST


class LyraAPIClient(LyraClient):
    """Exercise the retained private sync transport through the historical tests."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        return cast("Callable[..., Any]", getattr(self._transport, name))


class AsyncLyraAPIClient(AsyncLyraClient):
    """Exercise the retained private async transport through historical tests."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        return cast("Callable[..., Any]", getattr(self._transport, name))


class _FakeSyncResponseOptions(TypedDict):
    status_code: NotRequired[int]
    payload: NotRequired[JsonValue]
    text: NotRequired[str]
    headers: NotRequired[dict[str, str] | None]
    lines: NotRequired[list[str] | None]
    chunks: NotRequired[list[bytes] | None]


class _SyncRequestOptions(TypedDict):
    params: dict[str, Any] | None
    json: dict[str, Any] | None
    timeout: float
    headers: dict[str, str]


class FakeSyncResponse:
    def __init__(self, **options: Unpack[_FakeSyncResponseOptions]) -> None:
        self.status_code = options.get("status_code", 200)
        self._payload = options.get("payload")
        self.text = options.get("text", "")
        self.headers = options.get("headers") or {"content-type": "application/json"}
        self._lines = options.get("lines") or []
        self._chunks = options.get("chunks") or []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def json(self) -> JsonValue:
        assert self._payload is not None
        return self._payload

    def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:  # noqa: ARG002
        yield from self._lines

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:  # noqa: ARG002
        yield from self._chunks


def _job_response(*, reused: bool = False) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "reused": reused,
        "links": {
            "self": "/jobs/job-1",
            "events": "/jobs/job-1/events",
            "result": "/jobs/job-1/result",
        },
    }


def _status_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "running",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _job_list_response() -> dict[str, Any]:
    return {
        "jobs": [
            {
                "job_id": "job-1",
                "metric": "heavy_metric",
                "status": "running",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    }


def _job_cancel_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "status": "cancelled",
        "cancellation_requested": True,
        "revoke_requested": True,
    }


def _liveness_response() -> dict[str, Any]:
    return {
        "status": "ok",
        "api_version": "0.1.0",
    }


def _readiness_response() -> dict[str, Any]:
    return {
        "status": "ready",
        "api_version": "0.1.0",
        "redis": {"status": "ok"},
        "database": {"status": "ok"},
    }


def _database_unavailable_response() -> dict[str, Any]:
    return {
        "detail": {
            "code": "database_unavailable",
            "message": "The spatial database is temporarily unavailable.",
            "retryable": True,
        }
    }


def _admin_status_response() -> dict[str, Any]:
    return {
        "api_version": "0.1.0",
        "redis": {"status": "ok"},
        "metric_count": 1,
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "configured_worker_count": 1,
        "job_store_ttl_seconds": 86400,
        "catalog_fingerprint": "abc",
    }


def _config_summary_response() -> dict[str, Any]:
    return {
        "api_host": DEFAULT_API_HOST,
        "api_port": 5219,
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "workers": [
            {
                "name": "interactive",
                "queues": ["interactive"],
                "concurrency": 1,
                "install_dir": "/lyra_data/plugins/runners/interactive",
                "temp_dir": "/lyra_data/cache/jobs/interactive",
            }
        ],
        "job_store_ttl_seconds": 86400,
        "plugin_catalog_dir": "/lyra_data/plugins/catalog",
        "plugin_state_path": "/lyra_data/state/plugins.toml",
        "plugin_runner_base_dir": "/lyra_data/plugins/runners",
    }


def _catalog_summary_response() -> dict[str, Any]:
    return {
        "metric_count": 1,
        "metric_names": ["smoke_table_metric"],
        "catalog_fingerprint": "abc",
        "plugin_sources": [
            {
                "id": "smoke",
                "source": "dir:///plugins/smoke",
                "source_kind": "directory",
                "ref": None,
                "enabled": True,
            }
        ],
        "metric_queues": {"smoke_table_metric": "interactive"},
    }


def _workers_response() -> dict[str, Any]:
    return {
        "inspect_available": True,
        "inspect_metadata": {
            "observed_at": "2026-01-01T00:00:00Z",
            "age_seconds": 0.25,
            "stale": False,
            "last_error": None,
        },
        "workers": [
            {
                "name": "interactive",
                "configured": True,
                "observed": True,
                "status": "online",
                "queues": ["interactive"],
                "active_count": 1,
                "reserved_count": 0,
                "scheduled_count": 0,
            }
        ],
    }


def _worker_detail_response() -> dict[str, Any]:
    return _workers_response()["workers"][0] | {
        "active_tasks": [{"id": "job-1", "name": "lyra.run_metric"}],
        "reserved_tasks": [],
        "scheduled_tasks": [],
        "stats": {"hostname": "interactive"},
        "inspect_metadata": _workers_response()["inspect_metadata"],
    }


def _queues_response() -> dict[str, Any]:
    return {
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "inspect_metadata": {
            "observed_at": "2026-01-01T00:00:00Z",
            "age_seconds": 0.25,
            "stale": False,
            "last_error": None,
        },
        "queues": [
            {
                "name": "interactive",
                "is_default": True,
                "assigned_metric_count": 1,
                "configured_workers": ["interactive"],
                "observed_workers": ["interactive"],
                "pending_depth": None,
                "pending_depth_unknown": True,
            }
        ],
    }


def _met_zone_response() -> dict[str, Any]:
    return {
        "cve_met": "0901",
        "nom_met": "Valle de Mexico",
    }


def _plugin_repo_response() -> dict[str, Any]:
    return {
        "id": "smoke",
        "source": "dir:///plugins/smoke",
        "ref": None,
        "enabled": True,
    }


def _plugin_repo_list_response() -> dict[str, Any]:
    return {"repos": [_plugin_repo_response()]}


def _delete_plugin_repo_response() -> dict[str, Any]:
    return {
        "deleted": True,
        "repo_id": "smoke",
        "removed_metric_queues": ["smoke_table_metric"],
        "catalog_refresh": _plugin_catalog_refresh_status(),
    }


def _sync_plugin_repo_response() -> dict[str, Any]:
    return {
        "repo_id": "smoke",
        "changed": True,
        "display_name": "smoke",
        "catalog_refresh": _plugin_catalog_refresh_status(),
    }


def _create_plugin_repo_response() -> dict[str, Any]:
    return {
        "repo": _plugin_repo_response(),
        "catalog_refresh": _plugin_catalog_refresh_status(),
    }


def _update_plugin_repo_response() -> dict[str, Any]:
    repo = _plugin_repo_response()
    repo["source"] = "dir:///plugins/smoke-updated"
    repo["enabled"] = False
    return {
        "repo": repo,
        "catalog_refresh": _plugin_catalog_refresh_status(),
    }


def _plugin_catalog_refresh_status() -> dict[str, Any]:
    return {
        "refreshed": True,
        "error": None,
        "catalog_changed": True,
        "previous_catalog_fingerprint": "before",
        "catalog_fingerprint": "after",
        "assigned_metric_queues": ["smoke_table_metric"],
        "removed_metric_queues": [],
        "workers_restart_recommended": True,
    }


def _plugin_catalog_refresh_response() -> dict[str, Any]:
    return {
        "updated_plugins": ["smoke"],
        "catalog_changed": True,
        "previous_catalog_fingerprint": "before",
        "catalog_fingerprint": "after",
        "assigned_metric_queues": ["smoke_table_metric"],
        "removed_metric_queues": [],
        "workers_restarted": False,
        "workers_restart_recommended": True,
        "message": "Plugin catalog refreshed.",
    }


def _worker_restart_response() -> dict[str, Any]:
    return {
        "requested": True,
        "timeout": 12.5,
        "message": "Worker restart requested.",
    }


def _plugin_routing_response() -> dict[str, Any]:
    return {
        "metric_queues": {"smoke_table_metric": "interactive"},
        "allowed_queues": ["interactive", "batch"],
        "default_queue": "interactive",
    }


def _metric_queue_assignment_response() -> dict[str, Any]:
    return {
        "metric_name": "smoke_table_metric",
        "queue": "batch",
    }


def _delete_metric_queue_response() -> dict[str, Any]:
    return {
        "deleted": True,
        "metric_name": "smoke_table_metric",
    }


def _terminal_event_lines(event_id: str = "1-0") -> list[str]:
    event = {
        "kind": "lifecycle",
        "job_id": "job-1",
        "metric": "heavy_metric",
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "succeeded",
    }
    return [
        f"id: {event_id}",
        "event: lifecycle",
        f"data: {json.dumps(event)}",
        "",
    ]


def _progress_event_lines(event_id: str = "1-0") -> list[str]:
    event = {
        "kind": "progress",
        "job_id": "job-1",
        "metric": "heavy_metric",
        "timestamp": "2026-01-01T00:00:00Z",
        "stage": "compute",
        "current": 1,
        "total": 2,
    }
    return [
        f"id: {event_id}",
        "event: progress",
        f"data: {json.dumps(event)}",
        "",
    ]


def _result_response() -> dict[str, Any]:
    return {
        "kind": "table",
        "job_id": "job-1",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": ["value"],
        "data": [[6]],
    }


def _result_descriptor_response() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": "job-1",
        "status": "succeeded",
        "result_kind": "table",
        "result_ref": "lyra://results/job-1",
        "provenance": {
            "metric": "heavy_metric",
            "catalog_fingerprint": "catalog-1",
            "plugin": {"name": "fake-plugin", "version": "1.0.0"},
            "input": {
                "location": {"data_type": "met_zone_code", "value": "09.01"},
                "value": 3,
            },
            "output": {
                "kind": "table",
                "columns": [
                    {
                        "name": "value",
                        "type": "integer",
                        "unit": "count",
                        "description": "Example output value.",
                        "nullable": False,
                    }
                ],
                "batched_columns": [],
            },
            "created_at": "2026-07-09T12:00:00Z",
            "row_identity": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        },
        "completed_at": "2026-07-09T12:05:00Z",
        "lifetime": {"expires_in_seconds": 3600, "expires_at": None},
        "raw": {
            "result_ref": "lyra://results/job-1",
            "formats": ["terminal_json", "jsonl"],
            "terminal_json_path": "/jobs/job-1/result",
            "jsonl_path": "/jobs/job-1/result/table.jsonl",
        },
        "table": {
            "row_count": 1,
            "column_count": 1,
            "columns": ["value"],
            "column_contracts": [
                {
                    "name": "value",
                    "type": "integer",
                    "unit": "count",
                    "description": "Example output value.",
                    "nullable": False,
                }
            ],
            "index_field": "_result_index",
            "row_identity": {
                "field": "cvegeo",
                "namespace": "inegi:cvegeo:ageb",
                "version": "2020",
            },
        },
        "preview": {
            "index_field": "_result_index",
            "rows": [{"_result_index": "area-1", "value": 6}],
            "row_limit": 20,
            "truncated": False,
        },
        "summary": {
            "kind": "table",
            "row_count": 1,
            "column_count": 1,
            "columns": [
                {
                    "name": "value",
                    "count": 1,
                    "null_count": 0,
                    "numeric": {
                        "count": 1,
                        "null_count": 0,
                        "min": 6,
                        "max": 6,
                        "mean": 6.0,
                    },
                }
            ],
            "error": None,
        },
        "file": None,
        "error": None,
    }


def _file_result_response() -> dict[str, Any]:
    return {
        "kind": "file",
        "job_id": "job-1",
        "status": "succeeded",
        "file_path": "/lyra_data/cache/jobs/job-1/result.tif",
        "media_type": "image/tiff",
    }


def _data_types_response() -> dict[str, Any]:
    return {
        "location": [
            {
                "data_type": "geojson",
                "description": "GeoJSON locations.",
                "wrapper_schema": {"type": "object"},
            }
        ],
        "bounds": [
            {
                "data_type": "geojson",
                "description": "One GeoJSON bounds geometry.",
                "wrapper_schema": {"type": "object"},
            }
        ],
    }


def _metric_response() -> dict[str, Any]:
    return {
        "name": "accessibility_by_destination",
        "description": "Compute accessibility by destination.",
        "request_schema": {
            "type": "object",
            "required": ["location", "sector_filters"],
            "properties": {
                "location": {"type": "object"},
                "sector_filters": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "required": ["key", "value"],
                        "properties": {
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "output": {
            "kind": "table",
            "columns": [],
            "batched_columns": [
                {
                    "source": "sector_filters",
                    "name": "job_accessibility_{key}",
                    "type": "number",
                    "unit": "jobs",
                    "description": "Job accessibility for {label}.",
                    "nullable": False,
                }
            ],
        },
    }


def _metric_catalog_response() -> dict[str, Any]:
    return {
        "client_schema_version": 1,
        "json_schema_dialect": "https://json-schema.org/draft/2020-12/schema",
        "catalog_fingerprint": "abc123",
        "metrics": [_metric_response()],
    }


def test_sync_client_uses_job_api_for_job_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, Any]] = []

    def post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        posted.append(
            {"url": url, "json": json, "timeout": timeout, "headers": headers}
        )
        return FakeSyncResponse(status_code=202, payload=_job_response())

    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool = False,  # noqa: ARG001
    ) -> FakeSyncResponse:
        if url.endswith("/events"):
            return FakeSyncResponse(lines=_terminal_event_lines())
        if url.endswith("/result"):
            return FakeSyncResponse(payload=_result_response())
        return FakeSyncResponse(payload=_status_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.post", post)
    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    client = LyraAPIClient(
        "example.test",
        secure=False,
        timeout=12.0,
        agent_api_key="agent-secret",
    )

    job = client.create_job("heavy_metric", {"value": 3}, idempotency_key="key-1")
    status = client.get_job(job.job_id)
    events = list(client.iter_job_events(job.job_id))
    result = client.get_job_result(job.job_id)
    processed = client.raw.run("heavy_metric", {"value": 3})

    assert posted[0]["url"] == "http://example.test/jobs"
    assert posted[0]["json"] == {
        "metric": "heavy_metric",
        "input": {"value": 3},
        "idempotency_key": "key-1",
    }
    assert posted[0]["headers"] == {"Authorization": "Bearer agent-secret"}
    assert job.job_id == "job-1"
    assert job.reused is False
    assert status.status == "running"
    assert [event.event.name for event in events] == ["succeeded"]
    assert result.kind == "table"
    assert result.data == [[6]]
    assert isinstance(processed, TableJobResult)
    assert processed.data == [[6]]


def test_sync_job_handle_resumes_after_disconnect_and_dispatches_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_headers: list[dict[str, str]] = []
    progress_events: list[JobProgressEvent] = []

    class DisconnectingResponse(FakeSyncResponse):
        def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:
            del decode_unicode
            yield from _progress_event_lines()
            message = "stream disconnected"
            raise requests.ConnectionError(message)

    responses: Iterator[FakeSyncResponse] = iter(
        [DisconnectingResponse(), FakeSyncResponse(lines=_terminal_event_lines("2-0"))]
    )

    def post(
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        del url, json, timeout, headers
        return FakeSyncResponse(status_code=202, payload=_job_response())

    def get(
        url: str,
        *,
        timeout: float,
        headers: dict[str, str],
        stream: bool = False,
    ) -> FakeSyncResponse:
        del timeout, stream
        if url.endswith("/result"):
            return FakeSyncResponse(payload=_result_response())
        seen_headers.append(headers)
        return next(responses)

    monkeypatch.setattr("lyra.api.client.sync.requests.post", post)
    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    monkeypatch.setattr("lyra.api.client.sync.time.sleep", lambda _: None)
    client = LyraAPIClient("example.test", secure=False, agent_api_key="secret")

    result = client.submit_job("heavy_metric", {"value": 3}).wait(
        on_progress=progress_events.append
    )

    assert result.status == "succeeded"
    assert [event.current for event in progress_events] == [1]
    assert seen_headers == [
        {"Authorization": "Bearer secret"},
        {"Authorization": "Bearer secret", "Last-Event-ID": "1-0"},
    ]


def test_sync_client_uses_admin_job_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests_seen: list[dict[str, Any]] = []

    def get(
        url: str,
        *,
        params: dict[str, int | str],
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        requests_seen.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        return FakeSyncResponse(payload=_job_list_response())

    def post(
        url: str,
        *,
        timeout: float,
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        requests_seen.append({"url": url, "timeout": timeout, "headers": headers})
        return FakeSyncResponse(payload=_job_cancel_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    monkeypatch.setattr("lyra.api.client.sync.requests.post", post)
    client = LyraAPIClient(
        "example.test",
        secure=False,
        timeout=12.0,
        agent_api_key="agent-secret",
        admin_api_key="admin-secret",
    )

    jobs = client.list_admin_jobs(limit=10, status="running", metric="heavy_metric")
    cancelled = client.cancel_admin_job("job-1")

    assert requests_seen == [
        {
            "url": "http://example.test/admin/jobs",
            "params": {
                "limit": 10,
                "status": "running",
                "metric": "heavy_metric",
            },
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "url": "http://example.test/admin/jobs/job-1/cancel",
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
    ]
    assert [job.job_id for job in jobs.jobs] == ["job-1"]
    assert cancelled.job_id == "job-1"
    assert cancelled.status == "cancelled"


def test_sync_client_uses_observability_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "http://example.test/live": _liveness_response(),
        "http://example.test/ready": _readiness_response(),
        "http://example.test/admin/status": _admin_status_response(),
        "http://example.test/admin/config-summary": _config_summary_response(),
        "http://example.test/admin/catalog": _catalog_summary_response(),
        "http://example.test/admin/workers": _workers_response(),
        "http://example.test/admin/workers/interactive": _worker_detail_response(),
        "http://example.test/admin/queues": _queues_response(),
    }
    seen: list[str] = []

    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        seen.append(url)
        return FakeSyncResponse(payload=responses[url])

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    client = LyraAPIClient("example.test", secure=False)

    liveness = client.get_liveness()
    readiness = client.get_readiness()
    status = client.get_admin_status()
    config = client.get_admin_config_summary()
    catalog = client.get_admin_catalog()
    workers = client.get_admin_workers()
    worker = client.get_admin_worker("interactive")
    queues = client.get_admin_queues()

    assert seen == list(responses)
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert readiness.database.status == "ok"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.plugin_sources[0].source_kind == "directory"
    assert workers.workers[0].status == "online"
    assert workers.inspect_metadata.stale is False
    assert worker.active_tasks[0].id == "job-1"
    assert worker.inspect_metadata.age_seconds == 0.25
    assert queues.queues[0].pending_depth_unknown is True


def test_sync_client_exposes_structured_database_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lyra.api.client.sync.requests.request",
        lambda *_args, **_kwargs: FakeSyncResponse(
            status_code=503,
            payload=_database_unavailable_response(),
            headers={"Retry-After": "5"},
        ),
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        LyraAPIClient("example.test", secure=False).get_met_zone_code("Guadalajara")

    assert exc_info.value.code == "database_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5


def test_sync_client_uses_lookup_plugin_and_routing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _met_zone_response(),
        _plugin_repo_list_response(),
        _create_plugin_repo_response(),
        _update_plugin_repo_response(),
        _delete_plugin_repo_response(),
        _sync_plugin_repo_response(),
        _plugin_catalog_refresh_response(),
        _worker_restart_response(),
        _plugin_routing_response(),
        _metric_queue_assignment_response(),
        _delete_metric_queue_response(),
    ]
    requests_seen: list[dict[str, Any]] = []

    def request(
        method: str,
        url: str,
        **options: Unpack[_SyncRequestOptions],
    ) -> FakeSyncResponse:
        requests_seen.append(
            {
                "method": method,
                "url": url,
                "params": options["params"],
                "json": options["json"],
                "timeout": options["timeout"],
                "headers": options["headers"],
            }
        )
        return FakeSyncResponse(payload=responses.pop(0))

    monkeypatch.setattr("lyra.api.client.sync.requests.request", request)
    client = LyraAPIClient(
        "example.test/",
        secure=False,
        timeout=12.0,
        admin_api_key="admin-secret",
    )

    met_zone = client.get_met_zone_code("Valle de Mexico")
    repos = client.list_plugin_repos()
    created = client.create_plugin_repo("dir:///plugins/smoke", repo_id="smoke")
    updated = client.update_plugin_repo(
        "smoke",
        source="dir:///plugins/smoke-updated",
        enabled=False,
    )
    deleted = client.delete_plugin_repo("smoke")
    synced = client.sync_plugin_repo("smoke")
    refreshed = client.refresh_plugin_catalog()
    restarted = client.restart_workers(timeout=12.5)
    routing = client.list_plugin_routing()
    assignment = client.set_plugin_routing("smoke_table_metric", "batch")
    routing_deleted = client.delete_plugin_routing("smoke_table_metric")

    assert requests_seen == [
        {
            "method": "GET",
            "url": "http://example.test/lookups/met-zones",
            "params": {"name": "Valle de Mexico"},
            "json": None,
            "timeout": 12.0,
            "headers": {},
        },
        {
            "method": "GET",
            "url": "http://example.test/admin/plugin-repos",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "POST",
            "url": "http://example.test/admin/plugin-repos",
            "params": None,
            "json": {
                "source": "dir:///plugins/smoke",
                "id": "smoke",
                "enabled": True,
            },
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "PATCH",
            "url": "http://example.test/admin/plugin-repos/smoke",
            "params": None,
            "json": {
                "source": "dir:///plugins/smoke-updated",
                "enabled": False,
            },
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "DELETE",
            "url": "http://example.test/admin/plugin-repos/smoke",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "POST",
            "url": "http://example.test/admin/plugin-repos/smoke/sync",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "POST",
            "url": "http://example.test/admin/plugin-catalog/refresh",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "POST",
            "url": "http://example.test/admin/workers/restart",
            "params": {"timeout": 12.5},
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "GET",
            "url": "http://example.test/admin/plugin-routing",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "PUT",
            "url": "http://example.test/admin/plugin-routing/smoke_table_metric",
            "params": None,
            "json": {"queue": "batch"},
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
        {
            "method": "DELETE",
            "url": "http://example.test/admin/plugin-routing/smoke_table_metric",
            "params": None,
            "json": None,
            "timeout": 12.0,
            "headers": {"Authorization": "Bearer admin-secret"},
        },
    ]
    assert met_zone.cve_met == "0901"
    assert repos.repos[0].source == "dir:///plugins/smoke"
    assert created.repo.id == "smoke"
    assert updated.repo.enabled is False
    assert deleted.deleted is True
    assert synced.changed is True
    assert synced.catalog_refresh.refreshed is True
    assert refreshed.workers_restart_recommended is True
    assert restarted.timeout == 12.5
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}
    assert assignment.queue == "batch"
    assert routing_deleted.deleted is True


def test_sync_client_reports_operator_route_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def request(
        _method: str,
        _url: str,
        **_options: Unpack[_SyncRequestOptions],
    ) -> FakeSyncResponse:
        return FakeSyncResponse(status_code=409, text="plugin disabled")

    monkeypatch.setattr("lyra.api.client.sync.requests.request", request)

    with pytest.raises(
        DownloadError,
        match=r"Failed to sync plugin repo\. HTTP 409: plugin disabled",
    ):
        LyraAPIClient("example.test", secure=False).sync_plugin_repo("smoke")


def test_sync_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        assert url == "http://example.test/data-types"
        return FakeSyncResponse(payload=_data_types_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    response = LyraAPIClient("example.test", secure=False).get_data_types()

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_sync_client_returns_v4_metric_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        assert url == "http://example.test/metrics"
        return FakeSyncResponse(payload=_metric_catalog_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    catalog = LyraAPIClient("example.test", secure=False).get_metrics()

    assert catalog.catalog_fingerprint == "abc123"
    assert len(catalog.metrics) == 1
    assert catalog.metrics[0].name == "accessibility_by_destination"
    output = catalog.metrics[0].output.model_dump(mode="json")
    batched_column = output["batched_columns"][0]
    assert set(batched_column) == {
        "source",
        "name",
        "type",
        "unit",
        "description",
        "nullable",
    }
    assert batched_column["name"] == "job_accessibility_{key}"


def test_sync_client_returns_one_v4_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        assert url == "http://example.test/metrics/accessibility_by_destination"
        return FakeSyncResponse(payload=_metric_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    metric = LyraAPIClient("example.test", secure=False).get_metric(
        "accessibility_by_destination"
    )

    assert metric.name == "accessibility_by_destination"


def test_sync_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # noqa: ARG001
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        return FakeSyncResponse(payload={"location": []})

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)

    with pytest.raises(DownloadError, match="Invalid data types response format"):
        LyraAPIClient("example.test", secure=False).get_data_types()


def test_sync_client_downloads_file_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool,
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result/download"
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "image/tiff"},
            chunks=[b"abc", b"def"],
        )

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    output = tmp_path / "result.tif"

    LyraAPIClient("example.test", secure=False).download_job_result_to_file(
        "job-1",
        output,
    )

    assert output.read_bytes() == b"abcdef"


def test_sync_client_fetches_file_result_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result"
        return FakeSyncResponse(payload=_file_result_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)

    result = LyraAPIClient("example.test", secure=False).get_job_result("job-1")

    assert isinstance(result, FileJobResult)
    assert result.file_path == "/lyra_data/cache/jobs/job-1/result.tif"
    assert result.media_type == "image/tiff"


def test_result_ref_parser_accepts_refs_and_raw_job_ids() -> None:
    assert parse_result_ref("lyra://results/job-1") == "job-1"
    assert parse_result_ref("job-1") == "job-1"

    with pytest.raises(DownloadError, match="Invalid Lyra result reference"):
        parse_result_ref("lyra://results/job-1/extra")

    with pytest.raises(DownloadError, match="Unsupported result reference"):
        parse_result_ref("https://example.test/results/job-1")


def test_sync_client_fetches_result_descriptor_from_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def request(
        method: str,
        url: str,
        **options: Unpack[_SyncRequestOptions],
    ) -> FakeSyncResponse:
        seen.append(f"{method} {url}")
        assert options["headers"] == {"Authorization": "Bearer agent-secret"}
        return FakeSyncResponse(payload=_result_descriptor_response())

    monkeypatch.setattr("lyra.api.client.sync.requests.request", request)

    descriptor = LyraAPIClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).get_result_descriptor("lyra://results/job-1")

    assert seen == ["GET http://example.test/jobs/job-1/result/descriptor"]
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.table is not None
    assert descriptor.table.columns == ["value"]
    assert descriptor.table.column_contracts[0].unit == "count"
    assert descriptor.provenance is not None
    assert descriptor.provenance.plugin.version == "1.0.0"
    assert descriptor.preview.rows == [{"_result_index": "area-1", "value": 6}]


def test_sync_client_downloads_jsonl_result_from_raw_job_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],
        stream: bool,
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result/table.jsonl"
        assert headers == {"Authorization": "Bearer agent-secret"}
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "application/x-ndjson"},
            chunks=[b'{"_result_index":"area-1",', b'"value":6}\n'],
        )

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    output = tmp_path / "result.jsonl"

    LyraAPIClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).download_result("job-1", output)

    assert output.read_text() == '{"_result_index":"area-1","value":6}\n'


def test_sync_client_reports_result_download_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # noqa: ARG001
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool,  # noqa: ARG001
    ) -> FakeSyncResponse:
        return FakeSyncResponse(status_code=409, text="result is not a table")

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)

    with pytest.raises(
        DownloadError,
        match=r"Failed to download result\. HTTP 409: result is not a table",
    ):
        LyraAPIClient("example.test", secure=False).download_result(
            "job-1",
            tmp_path / "result.jsonl",
        )


def test_result_dataframe_requires_optional_pandas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def import_module(name: str) -> object:
        if name == "pandas":
            raise ImportError(name)
        raise AssertionError(name)

    monkeypatch.setattr("lyra.api.client.base.importlib.import_module", import_module)

    with pytest.raises(DownloadError, match="pandas is required"):
        LyraAPIClient("example.test", secure=False).result_dataframe("job-1")


def test_sync_client_hydrates_result_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePandas:
        @staticmethod
        def read_json(path: Path, *, lines: bool) -> dict[str, Any]:
            assert lines is True
            return {"path_exists": path.exists(), "content": path.read_text()}

    def import_module(name: str) -> object:
        assert name == "pandas"
        return FakePandas

    def get(
        url: str,  # noqa: ARG001
        *,
        timeout: float,  # noqa: ARG001
        headers: dict[str, str],  # noqa: ARG001
        stream: bool,  # noqa: ARG001
    ) -> FakeSyncResponse:
        return FakeSyncResponse(chunks=[b'{"_result_index":"area-1","value":6}\n'])

    monkeypatch.setattr("lyra.api.client.sync.requests.get", get)
    monkeypatch.setattr("lyra.api.client.base.importlib.import_module", import_module)

    frame = LyraAPIClient("example.test", secure=False).result_dataframe(
        "lyra://results/job-1"
    )

    assert frame == {
        "path_exists": True,
        "content": '{"_result_index":"area-1","value":6}\n',
    }


class FakeContent:
    def __init__(
        self,
        *,
        lines: list[str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.lines = lines or []
        self.chunks = chunks or []

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iter_lines()

    async def _iter_lines(self) -> AsyncIterator[bytes]:
        for line in self.lines:
            yield f"{line}\n".encode()

    async def iter_chunked(
        self,
        chunk_size: int,  # noqa: ARG002
    ) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class _FakeAsyncResponseOptions(TypedDict):
    status: NotRequired[int]
    payload: NotRequired[JsonValue]
    text: NotRequired[str]
    headers: NotRequired[dict[str, str] | None]
    lines: NotRequired[list[str] | None]
    chunks: NotRequired[list[bytes] | None]


class FakeAsyncResponse:
    def __init__(self, **options: Unpack[_FakeAsyncResponseOptions]) -> None:
        self.status = options.get("status", 200)
        self._payload = options.get("payload")
        self._text = options.get("text", "")
        self.headers = options.get("headers") or {"content-type": "application/json"}
        self.content = FakeContent(
            lines=options.get("lines"),
            chunks=options.get("chunks"),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def json(self) -> JsonValue:
        assert self._payload is not None
        return self._payload

    async def text(self) -> str:
        return self._text


class FakeSession:
    responses: ClassVar[list[FakeAsyncResponse]] = []

    def __init__(self, **_: object) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def post(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)

    def get(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)

    def request(self, *_: object, **__: object) -> FakeAsyncResponse:
        return self.responses.pop(0)


class FakeAsyncFile:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: Any = None

    async def __aenter__(self) -> Self:
        self._file = self._path.open("wb")
        return self

    async def __aexit__(self, *args: object) -> None:
        assert self._file is not None
        self._file.close()

    async def write(self, chunk: bytes) -> int:
        assert self._file is not None
        return self._file.write(chunk)


def test_async_client_processes_json_job(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(status=202, payload=_job_response()),
        FakeAsyncResponse(lines=_terminal_event_lines()),
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    result = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).raw.run(
            "heavy_metric",
            {"value": 3},
        )
    )

    assert result.kind == "table"
    assert result.data == [[6]]


def test_async_client_exposes_idempotent_replay_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        posted_json: ClassVar[dict[str, Any] | None] = None

        def post(self, *_: object, **kwargs: object) -> FakeAsyncResponse:
            posted_json = kwargs.get("json")
            assert isinstance(posted_json, dict)
            type(self).posted_json = cast("dict[str, Any]", posted_json)
            return super().post()

    RecordingSession.responses = [
        FakeAsyncResponse(status=202, payload=_job_response(reused=True))
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )

    response = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).create_job(
            "heavy_metric",
            {"value": 3},
            idempotency_key="retry-key",
        )
    )

    assert response.reused is True
    assert RecordingSession.posted_json == {
        "metric": "heavy_metric",
        "input": {"value": 3},
        "idempotency_key": "retry-key",
    }


def test_async_client_uses_admin_job_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"method": "GET", "args": args, "kwargs": kwargs})
            return super().get(*args, **kwargs)

        def post(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append(
                {"method": "POST", "args": args, "kwargs": kwargs}
            )
            return super().post(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_job_list_response()),
        FakeAsyncResponse(payload=_job_cancel_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAPIClient(
        "example.test",
        secure=False,
        timeout=12.0,
        agent_api_key="agent-secret",
        admin_api_key="admin-secret",
    )

    jobs = asyncio.run(
        client.list_admin_jobs(limit=10, status="running", metric="heavy_metric")
    )
    cancelled = asyncio.run(client.cancel_admin_job("job-1"))

    assert RecordingSession.requests_seen == [
        {
            "method": "GET",
            "args": ("http://example.test/admin/jobs",),
            "kwargs": {
                "params": {
                    "limit": 10,
                    "status": "running",
                    "metric": "heavy_metric",
                },
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "method": "POST",
            "args": ("http://example.test/admin/jobs/job-1/cancel",),
            "kwargs": {"headers": {"Authorization": "Bearer admin-secret"}},
        },
    ]
    assert [job.job_id for job in jobs.jobs] == ["job-1"]
    assert cancelled.job_id == "job-1"
    assert cancelled.status == "cancelled"


def test_async_client_uses_observability_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        urls: ClassVar[list[str]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.urls.append(str(args[0]))
            return super().get(*args, **kwargs)

    responses = [
        _liveness_response(),
        _readiness_response(),
        _admin_status_response(),
        _config_summary_response(),
        _catalog_summary_response(),
        _workers_response(),
        _worker_detail_response(),
        _queues_response(),
    ]
    RecordingSession.responses = [
        FakeAsyncResponse(payload=response) for response in responses
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAPIClient("example.test", secure=False)

    liveness = asyncio.run(client.get_liveness())
    readiness = asyncio.run(client.get_readiness())
    status = asyncio.run(client.get_admin_status())
    config = asyncio.run(client.get_admin_config_summary())
    catalog = asyncio.run(client.get_admin_catalog())
    workers = asyncio.run(client.get_admin_workers())
    worker = asyncio.run(client.get_admin_worker("interactive"))
    queues = asyncio.run(client.get_admin_queues())

    assert RecordingSession.urls == [
        "http://example.test/live",
        "http://example.test/ready",
        "http://example.test/admin/status",
        "http://example.test/admin/config-summary",
        "http://example.test/admin/catalog",
        "http://example.test/admin/workers",
        "http://example.test/admin/workers/interactive",
        "http://example.test/admin/queues",
    ]
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.plugin_sources[0].source_kind == "directory"
    assert workers.workers[0].status == "online"
    assert workers.inspect_metadata.stale is False
    assert worker.active_tasks[0].id == "job-1"
    assert worker.inspect_metadata.age_seconds == 0.25
    assert queues.queues[0].pending_depth_unknown is True


def test_async_client_exposes_structured_database_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(
            status=503,
            payload=_database_unavailable_response(),
            headers={"Retry-After": "5"},
        )
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        asyncio.run(
            AsyncLyraAPIClient("example.test", secure=False).get_met_zone_code(
                "Guadalajara"
            )
        )

    assert exc_info.value.code == "database_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5


def test_async_client_uses_lookup_plugin_and_routing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().request(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_met_zone_response()),
        FakeAsyncResponse(payload=_plugin_repo_list_response()),
        FakeAsyncResponse(payload=_create_plugin_repo_response()),
        FakeAsyncResponse(payload=_update_plugin_repo_response()),
        FakeAsyncResponse(payload=_delete_plugin_repo_response()),
        FakeAsyncResponse(payload=_sync_plugin_repo_response()),
        FakeAsyncResponse(payload=_plugin_catalog_refresh_response()),
        FakeAsyncResponse(payload=_worker_restart_response()),
        FakeAsyncResponse(payload=_plugin_routing_response()),
        FakeAsyncResponse(payload=_metric_queue_assignment_response()),
        FakeAsyncResponse(payload=_delete_metric_queue_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAPIClient(
        "example.test/",
        secure=False,
        admin_api_key="admin-secret",
    )

    async def run_requests() -> tuple[Any, ...]:
        return (
            await client.get_met_zone_code("Valle de Mexico"),
            await client.list_plugin_repos(),
            await client.create_plugin_repo(
                "dir:///plugins/smoke",
                repo_id="smoke",
            ),
            await client.update_plugin_repo(
                "smoke",
                source="dir:///plugins/smoke-updated",
                enabled=False,
            ),
            await client.delete_plugin_repo("smoke"),
            await client.sync_plugin_repo("smoke"),
            await client.refresh_plugin_catalog(),
            await client.restart_workers(timeout=12.5),
            await client.list_plugin_routing(),
            await client.set_plugin_routing("smoke_table_metric", "batch"),
            await client.delete_plugin_routing("smoke_table_metric"),
        )

    (
        met_zone,
        repos,
        created,
        updated,
        deleted,
        synced,
        refreshed,
        restarted,
        routing,
        assignment,
        routing_deleted,
    ) = asyncio.run(run_requests())

    assert RecordingSession.requests_seen == [
        {
            "args": ("GET", "http://example.test/lookups/met-zones"),
            "kwargs": {
                "params": {"name": "Valle de Mexico"},
                "json": None,
                "headers": {},
            },
        },
        {
            "args": ("GET", "http://example.test/admin/plugin-repos"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("POST", "http://example.test/admin/plugin-repos"),
            "kwargs": {
                "params": None,
                "json": {
                    "source": "dir:///plugins/smoke",
                    "id": "smoke",
                    "enabled": True,
                },
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("PATCH", "http://example.test/admin/plugin-repos/smoke"),
            "kwargs": {
                "params": None,
                "json": {
                    "source": "dir:///plugins/smoke-updated",
                    "enabled": False,
                },
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("DELETE", "http://example.test/admin/plugin-repos/smoke"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("POST", "http://example.test/admin/plugin-repos/smoke/sync"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("POST", "http://example.test/admin/plugin-catalog/refresh"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("POST", "http://example.test/admin/workers/restart"),
            "kwargs": {
                "params": {"timeout": 12.5},
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": ("GET", "http://example.test/admin/plugin-routing"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": (
                "PUT",
                "http://example.test/admin/plugin-routing/smoke_table_metric",
            ),
            "kwargs": {
                "params": None,
                "json": {"queue": "batch"},
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
        {
            "args": (
                "DELETE",
                "http://example.test/admin/plugin-routing/smoke_table_metric",
            ),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer admin-secret"},
            },
        },
    ]
    assert met_zone.cve_met == "0901"
    assert repos.repos[0].source == "dir:///plugins/smoke"
    assert created.repo.id == "smoke"
    assert updated.repo.enabled is False
    assert deleted.deleted is True
    assert synced.changed is True
    assert synced.catalog_refresh.refreshed is True
    assert refreshed.workers_restart_recommended is True
    assert restarted.timeout == 12.5
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}
    assert assignment.queue == "batch"
    assert routing_deleted.deleted is True


def test_async_client_reports_operator_route_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:  # noqa: ARG002
            return FakeAsyncResponse(status=409, text="plugin disabled")

    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )

    with pytest.raises(
        DownloadError,
        match=r"Failed to sync plugin repo\. HTTP 409: plugin disabled",
    ):
        asyncio.run(
            AsyncLyraAPIClient("example.test", secure=False).sync_plugin_repo("smoke")
        )


def test_async_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_data_types_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    response = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).get_data_types()
    )

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_async_client_returns_v4_metric_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_catalog_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    catalog = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).get_metrics()
    )

    assert catalog.catalog_fingerprint == "abc123"
    assert len(catalog.metrics) == 1
    assert catalog.metrics[0].name == "accessibility_by_destination"
    output = catalog.metrics[0].output.model_dump(mode="json")
    batched_column = output["batched_columns"][0]
    assert set(batched_column) == {
        "source",
        "name",
        "type",
        "unit",
        "description",
        "nullable",
    }
    assert batched_column["description"] == "Job accessibility for {label}."


def test_async_client_returns_one_v4_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    metric = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).get_metric(
            "accessibility_by_destination"
        )
    )

    assert metric.name == "accessibility_by_destination"


def test_async_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload={"location": []}),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    with pytest.raises(DownloadError, match="Invalid data types response format"):
        asyncio.run(AsyncLyraAPIClient("example.test", secure=False).get_data_types())


def test_async_client_downloads_file_job_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        urls: ClassVar[list[str]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            url = str(args[0])
            self.urls.append(url)
            return super().get(*args, **kwargs)

    def fake_aiofiles_open(path: Path, mode: str) -> FakeAsyncFile:
        assert mode == "wb"
        return FakeAsyncFile(path)

    RecordingSession.responses = [
        FakeAsyncResponse(headers={"content-type": "image/tiff"}, chunks=[b"abc"]),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    monkeypatch.setattr("lyra.api.client.async_.aiofiles.open", fake_aiofiles_open)
    output = tmp_path / "result.tif"

    asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).download_job_result_to_file(
            "job-1",
            output,
        )
    )

    assert RecordingSession.urls == [
        "http://example.test/jobs/job-1/result/download",
    ]
    assert output.read_bytes() == b"abc"


def test_async_client_fetches_file_result_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_file_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    result = asyncio.run(
        AsyncLyraAPIClient("example.test", secure=False).get_job_result("job-1")
    )

    assert isinstance(result, FileJobResult)
    assert result.file_path == "/lyra_data/cache/jobs/job-1/result.tif"
    assert result.media_type == "image/tiff"


def test_async_client_fetches_result_descriptor_from_raw_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().request(*args, **kwargs)

    RecordingSession.responses = [
        FakeAsyncResponse(payload=_result_descriptor_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    client = AsyncLyraAPIClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    )

    descriptor = asyncio.run(client.get_result_descriptor("job-1"))

    assert RecordingSession.requests_seen == [
        {
            "args": ("GET", "http://example.test/jobs/job-1/result/descriptor"),
            "kwargs": {
                "params": None,
                "json": None,
                "headers": {"Authorization": "Bearer agent-secret"},
            },
        }
    ]
    assert descriptor.result_ref == "lyra://results/job-1"
    assert descriptor.summary.row_count == 1
    assert descriptor.completed_at.isoformat() == "2026-07-09T12:05:00+00:00"


def test_async_client_downloads_jsonl_result_from_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        requests_seen: ClassVar[list[dict[str, Any]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.requests_seen.append({"args": args, "kwargs": kwargs})
            return super().get(*args, **kwargs)

    def fake_aiofiles_open(path: Path, mode: str) -> FakeAsyncFile:
        assert mode == "wb"
        return FakeAsyncFile(path)

    RecordingSession.responses = [
        FakeAsyncResponse(chunks=[b'{"_result_index":"area-1","value":6}\n']),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )
    monkeypatch.setattr("lyra.api.client.async_.aiofiles.open", fake_aiofiles_open)
    output = tmp_path / "result.jsonl"

    asyncio.run(
        AsyncLyraAPIClient(
            "example.test",
            secure=False,
            agent_api_key="agent-secret",
        ).download_result(
            "lyra://results/job-1",
            output,
        )
    )

    assert RecordingSession.requests_seen == [
        {
            "args": ("http://example.test/jobs/job-1/result/table.jsonl",),
            "kwargs": {"headers": {"Authorization": "Bearer agent-secret"}},
        }
    ]
    assert output.read_text() == '{"_result_index":"area-1","value":6}\n'


def test_async_client_reports_result_download_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorSession(FakeSession):
        def get(self, *_: object, **__: object) -> FakeAsyncResponse:
            return FakeAsyncResponse(status=409, text="result is not a table")

    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        ErrorSession,
    )

    with pytest.raises(
        DownloadError,
        match=r"Failed to download result\. HTTP 409: result is not a table",
    ):
        asyncio.run(
            AsyncLyraAPIClient("example.test", secure=False).download_result(
                "job-1",
                tmp_path / "result.jsonl",
            )
        )
