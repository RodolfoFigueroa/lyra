import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar, NotRequired, Self, TypedDict, Unpack, cast

import pytest
import requests
from lyra.api import parse_result_ref
from lyra.api.client.async_ import AsyncLyraAdminClient, AsyncLyraClient
from lyra.api.client.sync import LyraAdminClient, LyraClient
from lyra.api.exceptions import DownloadError, ServiceUnavailableError
from lyra.api.options import SubmitOptions
from lyra.sdk.models.job import FileJobResult, TableJobResult
from lyra.sdk.types import JsonValue

from lyra_app.config import DEFAULT_API_HOST


def test_public_client_surfaces_and_credentials_are_separated() -> None:
    sync_consumer = LyraClient("example.test")
    async_consumer = AsyncLyraClient("example.test")
    sync_admin = LyraAdminClient("example.test")
    async_admin = AsyncLyraAdminClient("example.test")

    assert not hasattr(sync_consumer, "admin")
    assert not hasattr(async_consumer, "admin")
    assert not hasattr(sync_admin, "raw")
    assert not hasattr(async_admin, "raw")
    assert hasattr(sync_admin, "health")
    assert hasattr(async_admin, "health")

    sync_consumer_parameters = inspect.signature(LyraClient).parameters
    async_consumer_parameters = inspect.signature(AsyncLyraClient).parameters
    sync_admin_parameters = inspect.signature(LyraAdminClient).parameters
    async_admin_parameters = inspect.signature(AsyncLyraAdminClient).parameters

    assert "agent_api_key" in sync_consumer_parameters
    assert "agent_api_key" in async_consumer_parameters
    assert "admin_api_key" not in sync_consumer_parameters
    assert "admin_api_key" not in async_consumer_parameters
    assert "admin_api_key" in sync_admin_parameters
    assert "admin_api_key" in async_admin_parameters
    assert "agent_api_key" not in sync_admin_parameters
    assert "agent_api_key" not in async_admin_parameters


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
        self.text = options.get("text", json.dumps(self._payload))
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

    def iter_lines(self, *, decode_unicode: bool) -> Iterator[str]:  # ruff:ignore[unused-method-argument]
        yield from self._lines

    def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:  # ruff:ignore[unused-method-argument]
        yield from self._chunks


def _mock_sync_http(
    monkeypatch: pytest.MonkeyPatch,
    **handlers: Callable[..., FakeSyncResponse],
) -> None:
    for method, handler in handlers.items():
        monkeypatch.setattr(requests, method, handler)

    def request(
        method: str, url: str, **kwargs: Unpack[_SyncRequestOptions]
    ) -> FakeSyncResponse:
        options = {key: value for key, value in kwargs.items() if value is not None}
        assert method in {"GET", "POST"}
        handler = cast(
            "Callable[..., FakeSyncResponse]", getattr(requests, method.lower())
        )
        return handler(url, **options)

    monkeypatch.setattr(requests, "request", request)


def _job_response(*, reused: bool = False) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "reused": reused,
        "links": {
            "self": "/jobs/job-1",
            "result": "/jobs/job-1/result",
        },
    }


def _status_response() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _job_list_response() -> dict[str, Any]:
    return {
        "jobs": [
            {
                "job_id": "job-1",
                "metric": "heavy_metric",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
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
        "catalog_available": True,
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
        "catalog_available": True,
        "api_version": "0.1.0",
        "redis": {"status": "ok"},
        "metric_count": 1,
        "allowed_queues": ["interactive"],
        "default_queue": "interactive",
        "configured_worker_count": 1,
        "result_retention_seconds": 86400,
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
                "temp_dir": "/lyra_data/cache/jobs/interactive",
            }
        ],
        "result_retention_seconds": 86400,
    }


def _catalog_summary_response() -> dict[str, Any]:
    return {
        "catalog_available": True,
        "metric_count": 1,
        "metric_names": ["smoke_table_metric"],
        "catalog_fingerprint": "abc",
        "installed_plugins": [
            {
                "distribution": "lyra-smoke-plugin",
                "version": "0.1.0",
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
        "catalog_available": True,
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
        "distribution": "lyra-smoke-plugin",
        "version": "0.1.0",
        "enabled": True,
    }


def _plugin_repo_list_response() -> dict[str, Any]:
    return {"plugins": [_plugin_repo_response()]}


def _plugin_routing_response() -> dict[str, Any]:
    return {
        "metric_queues": {"smoke_table_metric": "interactive"},
        "allowed_queues": ["interactive", "batch"],
        "default_queue": "interactive",
    }


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
                "parameters": {"value": 3},
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
        "spatial_inputs": {"location": "location"},
        "request_schema": {
            "type": "object",
            "required": ["location", "parameters"],
            "properties": {
                "location": {"type": "object"},
                "parameters": {
                    "type": "object",
                    "required": ["sector_filters"],
                    "properties": {
                        "sector_filters": {"type": "array", "items": {"type": "string"}}
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "output": {
            "kind": "table",
            "columns": [
                {
                    "name": "job_accessibility",
                    "type": "number",
                    "unit": "jobs",
                    "description": "Job accessibility.",
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
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool = False,  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        if url.endswith("/result"):
            return FakeSyncResponse(payload=_result_response())
        return FakeSyncResponse(payload={**_status_response(), "status": "succeeded"})

    _mock_sync_http(monkeypatch, post=post)
    _mock_sync_http(monkeypatch, get=get)
    client = LyraClient(
        "example.test",
        secure=False,
        timeout=12.0,
        agent_api_key="agent-secret",
    )

    job = client.raw.create(
        "heavy_metric", {"value": 3}, options=SubmitOptions(idempotency_key="key-1")
    )
    status = client.jobs.get(job.job_id)
    result = client.results.get(job.job_id)
    FakeSession.responses = [
        FakeAsyncResponse(payload={**_status_response(), "status": "succeeded"}),
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)
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
    assert status.status == "succeeded"
    assert result.kind == "table"
    assert result.data == [[6]]
    assert isinstance(processed, TableJobResult)
    assert processed.data == [[6]]


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

    _mock_sync_http(monkeypatch, get=get)
    _mock_sync_http(monkeypatch, post=post)
    client = LyraAdminClient(
        "example.test",
        secure=False,
        timeout=12.0,
        admin_api_key="admin-secret",
    )

    jobs = client.jobs.list(limit=10, status="running", metric="heavy_metric")
    cancelled = client.jobs.cancel("job-1")

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
    seen_headers: list[dict[str, str]] = []

    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],
    ) -> FakeSyncResponse:
        seen.append(url)
        seen_headers.append(headers)
        return FakeSyncResponse(payload=responses[url])

    _mock_sync_http(monkeypatch, get=get)
    client = LyraAdminClient(
        "example.test",
        secure=False,
        admin_api_key="admin-secret",
    )

    liveness = client.health.liveness()
    readiness = client.health.readiness()
    status = client.status()
    config = client.config_summary()
    catalog = client.catalog.summary()
    workers = client.workers.list()
    worker = client.workers.get("interactive")
    queues = client.queues.list()

    assert seen == list(responses)
    assert seen_headers[:2] == [{}, {}]
    assert seen_headers[2:] == [{"Authorization": "Bearer admin-secret"}] * 6
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert readiness.database.status == "ok"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert workers.workers[0].status == "online"
    assert workers.inspect_metadata.stale is False
    assert worker.active_tasks[0].id == "job-1"
    assert worker.inspect_metadata.age_seconds == pytest.approx(0.25)
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
        LyraClient("example.test", secure=False).lookups.met_zone_code("Guadalajara")

    assert exc_info.value.code == "database_unavailable"
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 5


def test_sync_client_uses_lookup_plugin_and_routing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _met_zone_response(),
        _plugin_repo_list_response(),
        _plugin_routing_response(),
    ]
    requests_seen: list[dict[str, Any]] = []

    def request(
        method: str, url: str, **options: Unpack[_SyncRequestOptions]
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
    client = LyraClient("example.test/", secure=False, timeout=12.0)
    admin = LyraAdminClient(
        "example.test/", secure=False, timeout=12.0, admin_api_key="admin-secret"
    )
    met_zone = client.lookups.met_zone_code("Valle de Mexico")
    repos = admin.plugins.list()
    routing = admin.routing.list()
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
            "url": "http://example.test/admin/plugins",
            "params": None,
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
    ]
    assert met_zone.cve_met == "0901"
    assert repos.plugins[0].distribution == "lyra-smoke-plugin"
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}


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
        match=r"Failed to list installed plugins\. HTTP 409: plugin disabled",
    ):
        LyraAdminClient("example.test", secure=False).plugins.list()


def test_sync_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/data-types"
        return FakeSyncResponse(payload=_data_types_response())

    _mock_sync_http(monkeypatch, get=get)
    response = LyraClient("example.test", secure=False).catalog.data_types()

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_sync_client_returns_v5_metric_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/metrics"
        return FakeSyncResponse(payload=_metric_catalog_response())

    _mock_sync_http(monkeypatch, get=get)
    catalog = LyraClient("example.test", secure=False).catalog.metrics()

    assert catalog.catalog_fingerprint == "abc123"
    assert len(catalog.metrics) == 1
    assert catalog.metrics[0].name == "accessibility_by_destination"
    output = catalog.metrics[0].output.model_dump(mode="json")
    column = output["columns"][0]
    assert set(column) == {
        "name",
        "type",
        "unit",
        "description",
        "nullable",
    }
    assert column["name"] == "job_accessibility"


def test_sync_client_returns_one_v5_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/metrics/accessibility_by_destination"
        return FakeSyncResponse(payload=_metric_response())

    _mock_sync_http(monkeypatch, get=get)
    metric = LyraClient("example.test", secure=False).catalog.metric(
        "accessibility_by_destination"
    )

    assert metric.name == "accessibility_by_destination"


def test_sync_client_rejects_invalid_data_type_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # ruff:ignore[unused-function-argument]
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        return FakeSyncResponse(payload={"location": []})

    _mock_sync_http(monkeypatch, get=get)

    with pytest.raises(
        DownloadError, match="Failed to fetch data types: invalid JSON response"
    ):
        LyraClient("example.test", secure=False).catalog.data_types()


def test_sync_client_downloads_file_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool,
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result/download"
        assert stream is True
        return FakeSyncResponse(
            headers={"content-type": "image/tiff"},
            chunks=[b"abc", b"def"],
        )

    _mock_sync_http(monkeypatch, get=get)
    output = tmp_path / "result.tif"

    LyraClient("example.test", secure=False).results.download_file(
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
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        assert url == "http://example.test/jobs/job-1/result"
        return FakeSyncResponse(payload=_file_result_response())

    _mock_sync_http(monkeypatch, get=get)

    result = LyraClient("example.test", secure=False).results.get("job-1")

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

    descriptor = LyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).results.descriptor("lyra://results/job-1")

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
        timeout: float,  # ruff:ignore[unused-function-argument]
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

    _mock_sync_http(monkeypatch, get=get)
    output = tmp_path / "result.jsonl"

    LyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    ).results.download("job-1", output)

    assert output.read_text() == '{"_result_index":"area-1","value":6}\n'


def test_sync_client_reports_result_download_http_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get(
        url: str,  # ruff:ignore[unused-function-argument]
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool,  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        return FakeSyncResponse(status_code=409, text="result is not a table")

    _mock_sync_http(monkeypatch, get=get)

    with pytest.raises(
        DownloadError,
        match=r"Failed to download result\. HTTP 409: result is not a table",
    ):
        LyraClient("example.test", secure=False).results.download(
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
        LyraClient("example.test", secure=False).results.dataframe("job-1")


def test_sync_client_hydrates_result_dataframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePandas:
        @staticmethod
        def read_json(path: Path, *, lines: bool) -> dict[str, Any]:
            assert lines is True
            return {
                "path_exists": path.exists(),
                "content": path.read_text(encoding="utf-8"),
            }

    def import_module(name: str) -> object:
        assert name == "pandas"
        return FakePandas

    def get(
        url: str,  # ruff:ignore[unused-function-argument]
        *,
        timeout: float,  # ruff:ignore[unused-function-argument]
        headers: dict[str, str],  # ruff:ignore[unused-function-argument]
        stream: bool,  # ruff:ignore[unused-function-argument]
    ) -> FakeSyncResponse:
        return FakeSyncResponse(chunks=[b'{"_result_index":"area-1","value":6}\n'])

    _mock_sync_http(monkeypatch, get=get)
    monkeypatch.setattr("lyra.api.client.base.importlib.import_module", import_module)

    frame = LyraClient("example.test", secure=False).results.dataframe(
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
        chunk_size: int,  # ruff:ignore[unused-method-argument]
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
        self._text = options.get("text", json.dumps(self._payload))
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

    def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
        options = {key: value for key, value in kwargs.items() if value is not None}
        if args[0] == "GET":
            return self.get(*args[1:], **options)
        assert args[0] == "POST"
        return self.post(*args[1:], **options)


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
        FakeAsyncResponse(payload={**_status_response(), "status": "succeeded"}),
        FakeAsyncResponse(payload=_result_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    result = asyncio.run(
        AsyncLyraClient("example.test", secure=False).raw.run(
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
        AsyncLyraClient("example.test", secure=False).raw.create(
            "heavy_metric",
            {"value": 3},
            options=SubmitOptions(idempotency_key="retry-key"),
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
    client = AsyncLyraAdminClient(
        "example.test",
        secure=False,
        timeout=12.0,
        admin_api_key="admin-secret",
    )

    jobs = asyncio.run(
        client.jobs.list(limit=10, status="running", metric="heavy_metric")
    )
    cancelled = asyncio.run(client.jobs.cancel("job-1"))

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
        headers: ClassVar[list[dict[str, str]]] = []

        def get(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            self.urls.append(str(args[0]))
            headers = kwargs.get("headers")
            assert isinstance(headers, dict)
            self.headers.append(cast("dict[str, str]", headers))
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
    client = AsyncLyraAdminClient(
        "example.test",
        secure=False,
        admin_api_key="admin-secret",
    )

    liveness = asyncio.run(client.health.liveness())
    readiness = asyncio.run(client.health.readiness())
    status = asyncio.run(client.status())
    config = asyncio.run(client.config_summary())
    catalog = asyncio.run(client.catalog.summary())
    workers = asyncio.run(client.workers.list())
    worker = asyncio.run(client.workers.get("interactive"))
    queues = asyncio.run(client.queues.list())

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
    assert RecordingSession.headers[:2] == [{}, {}]
    assert (
        RecordingSession.headers[2:] == [{"Authorization": "Bearer admin-secret"}] * 6
    )
    assert liveness.status == "ok"
    assert readiness.status == "ready"
    assert status.metric_count == 1
    assert config.workers[0].name == "interactive"
    assert catalog.installed_plugins[0].distribution == "lyra-smoke-plugin"
    assert workers.workers[0].status == "online"
    assert workers.inspect_metadata.stale is False
    assert worker.active_tasks[0].id == "job-1"
    assert worker.inspect_metadata.age_seconds == pytest.approx(0.25)
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
            AsyncLyraClient("example.test", secure=False).lookups.met_zone_code(
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
        FakeAsyncResponse(payload=_plugin_routing_response()),
    ]
    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession", RecordingSession
    )
    client = AsyncLyraClient("example.test/", secure=False)
    admin = AsyncLyraAdminClient(
        "example.test/", secure=False, admin_api_key="admin-secret"
    )

    async def run_requests() -> tuple[Any, ...]:
        return (
            await client.lookups.met_zone_code("Valle de Mexico"),
            await admin.plugins.list(),
            await admin.routing.list(),
        )

    met_zone, repos, routing = asyncio.run(run_requests())
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
            "args": ("GET", "http://example.test/admin/plugins"),
            "kwargs": {
                "params": None,
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
    ]
    assert met_zone.cve_met == "0901"
    assert repos.plugins[0].distribution == "lyra-smoke-plugin"
    assert routing.metric_queues == {"smoke_table_metric": "interactive"}


def test_async_client_reports_operator_route_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingSession(FakeSession):
        def request(self, *args: object, **kwargs: object) -> FakeAsyncResponse:
            assert isinstance(self, RecordingSession)
            del args, kwargs
            return FakeAsyncResponse(status=409, text="plugin disabled")

    monkeypatch.setattr(
        "lyra.api.client.async_.aiohttp.ClientSession",
        RecordingSession,
    )

    with pytest.raises(
        DownloadError,
        match=r"Failed to list installed plugins\. HTTP 409: plugin disabled",
    ):
        asyncio.run(AsyncLyraAdminClient("example.test", secure=False).plugins.list())


def test_async_client_returns_grouped_data_type_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_data_types_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    response = asyncio.run(
        AsyncLyraClient("example.test", secure=False).catalog.data_types()
    )

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema == {"type": "object"}


def test_async_client_returns_v5_metric_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_catalog_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    catalog = asyncio.run(
        AsyncLyraClient("example.test", secure=False).catalog.metrics()
    )

    assert catalog.catalog_fingerprint == "abc123"
    assert len(catalog.metrics) == 1
    assert catalog.metrics[0].name == "accessibility_by_destination"
    output = catalog.metrics[0].output.model_dump(mode="json")
    column = output["columns"][0]
    assert set(column) == {
        "name",
        "type",
        "unit",
        "description",
        "nullable",
    }
    assert column["description"] == "Job accessibility."


def test_async_client_returns_one_v5_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSession.responses = [
        FakeAsyncResponse(payload=_metric_response()),
    ]
    monkeypatch.setattr("lyra.api.client.async_.aiohttp.ClientSession", FakeSession)

    metric = asyncio.run(
        AsyncLyraClient("example.test", secure=False).catalog.metric(
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

    with pytest.raises(
        DownloadError, match="Failed to fetch data types: invalid JSON response"
    ):
        asyncio.run(AsyncLyraClient("example.test", secure=False).catalog.data_types())


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
        AsyncLyraClient("example.test", secure=False).results.download_file(
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
        AsyncLyraClient("example.test", secure=False).results.get("job-1")
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
    client = AsyncLyraClient(
        "example.test",
        secure=False,
        agent_api_key="agent-secret",
    )

    descriptor = asyncio.run(client.results.descriptor("job-1"))

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
        AsyncLyraClient(
            "example.test",
            secure=False,
            agent_api_key="agent-secret",
        ).results.download(
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
            assert isinstance(self, ErrorSession)
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
            AsyncLyraClient("example.test", secure=False).results.download(
                "job-1",
                tmp_path / "result.jsonl",
            )
        )
