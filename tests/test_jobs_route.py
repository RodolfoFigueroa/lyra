import asyncio
import importlib
import json
import sys
from collections.abc import Callable, Iterator, MutableMapping
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ParamSpec, TypeVar, cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from lyra.sdk.models import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
    JobCreateRequest,
    TableJobResult,
)
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.metric import MetricCatalogResponse
from lyra.sdk.types import JsonValue
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app import job_store, job_submission, registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.mcp.tools import InProcessLyraBackend
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import admin, data_types, health, jobs, metrics
from tests.config_helpers import load_test_config, plugin_state_store

Parameters = ParamSpec("Parameters")
ReturnT = TypeVar("ReturnT")


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 4,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "metrics": [
            {
                "name": "heavy_metric",
                "description": "A heavy metric.",
                "inputs": {
                    "location": {"kind": "location"},
                    "value": {"kind": "integer"},
                },
                "output": {
                    "kind": "table",
                    "columns": [
                        {
                            "name": "value",
                            "type": "integer",
                            "unit": "count",
                            "description": "Example output value.",
                        }
                    ],
                },
            }
        ],
    }


def _batched_manifest() -> dict[str, Any]:
    return {
        "schema_version": 4,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "metrics": [
            {
                "name": "batched_metric",
                "description": "A batched metric.",
                "inputs": {
                    "location": {"kind": "location"},
                    "sector_filters": {
                        "kind": "batch",
                        "max_items": 5,
                        "value": {"kind": "string"},
                    },
                },
                "output": {
                    "kind": "table",
                    "batched_columns": [
                        {
                            "source": "sector_filters",
                            "name": "accessibility_{key}",
                            "type": "number",
                            "unit": "jobs",
                            "description": "Accessibility for {label}.",
                        }
                    ],
                },
            }
        ],
    }


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


def _feature_collection(feature_id: str = "area-1") -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": feature_id,
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-99.20, 19.30],
                            [-99.10, 19.30],
                            [-99.10, 19.40],
                            [-99.20, 19.40],
                            [-99.20, 19.30],
                        ]
                    ],
                },
                "properties": {},
            }
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def _spatial_payload(
    *,
    data_type: str = "geojson",
    value: JsonValue = None,
) -> dict[str, Any]:
    return {
        "location": {
            "data_type": data_type,
            "value": _feature_collection() if value is None else value,
        },
        "value": 3,
    }


def _patch_converter_map(monkeypatch: pytest.MonkeyPatch) -> None:
    def convert_cvegeos(cvegeos: list[str]) -> GeoJSON:
        assert cvegeos == ["090020001"]
        return GeoJSON.model_validate(_feature_collection("cvegeo-area"))

    def convert_met_zone(code: str) -> GeoJSON:
        return GeoJSON.model_validate(_feature_collection(f"met-{code}"))

    converter_map = {
        "location": {
            "geojson": lambda geojson: geojson,
            "cvegeo_list": convert_cvegeos,
            "met_zone_code": convert_met_zone,
        },
        "bounds": {
            "geojson": lambda geojson: geojson,
            "cvegeo_list": convert_cvegeos,
            "met_zone_code": convert_met_zone,
        },
    }
    monkeypatch.setitem(
        sys.modules,
        "lyra_app.converters",
        SimpleNamespace(converter_map=converter_map),
    )


class FakeRedisAsync:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.rate_limit_ttls: dict[str, int] = {}
        self.deleted: list[str] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    async def ping(self) -> bool:
        return self.available

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations.append((key, ex))
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        *args: str | int,
    ) -> int | list[int]:
        if not args:
            current = int(self.values.get(key, "0"))
            if current <= 0:
                return 0
            if current == 1:
                await self.delete(key)
            else:
                self.values[key] = str(current - 1)
            return 1
        if len(args) == 1:
            expected = str(args[0])
            if self.values.get(key) != expected:
                return 0
            await self.delete(key)
            return 1

        limit, window_seconds = (int(value) for value in args)
        current = int(self.values.get(key, "0"))
        if current >= limit:
            return [0, current, self.rate_limit_ttls.get(key, window_seconds)]
        current += 1
        self.values[key] = str(current)
        if current == 1:
            self.expirations.append((key, window_seconds))
            self.rate_limit_ttls[key] = window_seconds
        return [1, current, self.rate_limit_ttls[key]]

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    async def zremrangebyscore(
        self,
        key: str,
        min: str | float,  # noqa: A002
        max: float,  # noqa: A002
    ) -> None:
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)

    async def xrange(
        self,
        key: str,
        minimum: str,
        /,
        *,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if minimum.startswith("("):
            after_id = minimum[1:]
            records = [record for record in records if record[0] > after_id]
        elif minimum != job_store.STREAM_START:
            records = [record for record in records if record[0] >= minimum]
        return records if count is None else records[:count]

    async def xread(
        self,
        streams: dict[str, str],
        *,
        block: int,  # noqa: ARG002
        count: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        key, after_id = next(iter(streams.items()))
        records = self.streams.get(key, [])
        if after_id != job_store.STREAM_LATEST:
            records = [record for record in records if record[0] > after_id]
        else:
            records = []
        if count is not None:
            records = records[:count]
        return [(key, records)] if records else []


class FailingPingRedisAsync(FakeRedisAsync):
    async def ping(self) -> bool:
        raise RedisError


class FakeCelery:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_task(
        self,
        name: str,
        *,
        args: list[dict[str, Any]],
        queue: str,
        task_id: str,
    ) -> None:
        self.sent.append(
            {"name": name, "args": args, "queue": queue, "task_id": task_id}
        )


class FailOnceCelery(FakeCelery):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def send_task(
        self,
        name: str,
        *,
        args: list[dict[str, Any]],
        queue: str,
        task_id: str,
    ) -> None:
        if not self.failed:
            self.failed = True
            error = "dispatch failed"
            raise RuntimeError(error)
        super().send_task(name, args=args, queue=queue, task_id=task_id)


class ConcurrentFakeRedisAsync(FakeRedisAsync):
    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool = False,
    ) -> bool:
        acquired = await super().set(key, value, ex=ex, nx=nx)
        if key.startswith("jobs:idempotency:"):
            await asyncio.sleep(0)
        return acquired


class FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


class FakeAsyncPath:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def name(self) -> str:
        return self._path.name

    def __fspath__(self) -> str:
        return str(self._path)

    async def exists(self) -> bool:
        return self._path.exists()

    async def unlink(self) -> None:
        self._path.unlink()


@pytest.fixture(autouse=True)
def reset_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    async def run_inline(
        func: Callable[Parameters, ReturnT],
        /,
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> ReturnT:
        return func(*args, **kwargs)

    monkeypatch.setattr(job_submission.asyncio, "to_thread", run_inline)
    registry.reset_catalog()
    load_test_config(
        tmp_path,
        metric_queues={
            "batched_metric": "priority-lane",
            "heavy_metric": "priority-lane",
        },
    )
    monkeypatch.setattr(
        registry,
        "PluginStateStore",
        lambda *_args, **_kwargs: plugin_state_store(tmp_path, get_config()),
    )
    yield
    registry.reset_catalog()
    clear_config_cache()


def _use_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(manifest or _manifest()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    registry.refresh_catalog()


def _patch_redis(monkeypatch: pytest.MonkeyPatch, redis: FakeRedisAsync) -> None:
    async def keep_current_status(
        snapshot: job_store.JobStatusSnapshot,
    ) -> job_store.JobStatusSnapshot:
        return snapshot

    monkeypatch.setattr(jobs, "redis_client", redis)
    monkeypatch.setattr(jobs.job_store, "redis_client", redis)
    monkeypatch.setattr(jobs, "reconcile_celery_failure", keep_current_status)


async def _request_app(
    app: FastAPI,
    method: str,
    path: str,
    *,
    authorization: str | None = None,
) -> httpx.Response:
    headers = {} if authorization is None else {"Authorization": authorization}
    request_kwargs: dict[str, Any] = {"headers": headers}
    if method == "POST":
        request_kwargs["json"] = {"metric": "missing", "input": {}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **request_kwargs)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/jobs"),
        ("GET", "/jobs/missing"),
        ("GET", "/jobs/missing/events"),
        ("GET", "/jobs/missing/result"),
        ("GET", "/jobs/missing/result/descriptor"),
        ("GET", "/jobs/missing/result/table.jsonl"),
        ("GET", "/jobs/missing/result/download"),
    ],
)
@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [
        (None, 401),
        ("Basic agent-secret", 401),
        ("Bearer invalid-secret", 403),
        ("Bearer admin-secret", 403),
    ],
)
def test_job_lifecycle_routes_require_agent_bearer_token(
    method: str,
    path: str,
    authorization: str | None,
    expected_status: int,
) -> None:
    app = FastAPI()
    app.include_router(jobs.router)

    response = asyncio.run(_request_app(app, method, path, authorization=authorization))

    assert response.status_code == expected_status
    assert "secret" not in response.text
    if expected_status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/jobs"),
        ("GET", "/jobs/missing"),
        ("GET", "/jobs/missing/events"),
        ("GET", "/jobs/missing/result"),
        ("GET", "/jobs/missing/result/descriptor"),
        ("GET", "/jobs/missing/result/table.jsonl"),
        ("GET", "/jobs/missing/result/download"),
    ],
)
def test_job_lifecycle_routes_accept_agent_bearer_token(
    method: str,
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())
    monkeypatch.setattr(job_submission, "get_metric_entry", lambda _metric: None)
    app = FastAPI()
    app.include_router(jobs.router)

    response = asyncio.run(
        _request_app(app, method, path, authorization="Bearer agent-secret")
    )

    assert response.status_code == 404


def test_agent_token_cannot_authorize_admin_routes() -> None:
    app = FastAPI()
    app.include_router(admin.router)

    response = asyncio.run(
        _request_app(
            app,
            "GET",
            "/admin/status",
            authorization="Bearer agent-secret",
        )
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer agent-secret",
        "Bearer admin-secret",
        "Bearer invalid-secret",
    ],
)
def test_discovery_and_lookup_routes_remain_public(
    authorization: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lyra_app.routes import met_zone  # noqa: PLC0415

    monkeypatch.setattr(health, "redis_client", FakeRedisAsync())
    monkeypatch.setattr(
        metrics,
        "get_metric_catalog",
        lambda: MetricCatalogResponse(catalog_fingerprint="catalog-1", metrics=[]),
    )

    async def lookup_met_zone(_name: str, *, conn: object) -> tuple[str, str]:
        assert conn is not None
        return "09.01", "Valle de México"

    monkeypatch.setattr(
        met_zone,
        "get_met_zone_code_from_name_async",
        lookup_met_zone,
    )

    class FakeConnection:
        async def execute(self, *_: object) -> None:
            return None

    class FakeConnectionContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, *_: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> FakeConnectionContext:
            return FakeConnectionContext()

    app = FastAPI()
    app.state.database = SimpleNamespace(
        config=get_config(),
        require_async_engine=FakeEngine,
    )
    app.include_router(health.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)
    app.include_router(met_zone.router)

    responses = [
        asyncio.run(_request_app(app, "GET", path, authorization=authorization))
        for path in (
            "/live",
            "/ready",
            "/data-types",
            "/metrics",
            "/lookups/met-zones?name=Valle%20de%20México",
        )
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200, 200]


async def _body(response: StreamingResponse) -> str:
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        async for chunk in response.body_iterator
    ]
    return "".join(chunks)


async def _file_response_body(response: FileResponse) -> bytes:
    chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await response(
        {
            "type": "http",
            "method": "GET",
            "path": "/jobs/job-1/result/download",
            "headers": [],
        },
        receive,
        send,
    )
    return b"".join(chunks)


def test_create_job_dispatches_generic_task_to_state_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-1"))

    response = asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input=_spatial_payload(),
                idempotency_key="key-1",
            )
        )
    )

    assert response.model_dump() == {
        "job_id": "job-1",
        "metric": "heavy_metric",
        "status": "queued",
        "reused": False,
        "links": {
            "self": "/jobs/job-1",
            "events": "/jobs/job-1/events",
            "result": "/jobs/job-1/result",
        },
    }
    assert celery.sent == [
        {
            "name": "lyra.run_metric",
            "args": [
                {
                    "job_id": "job-1",
                    "metric": "heavy_metric",
                    "input": {"location": _feature_collection(), "value": 3},
                    "idempotency_key": "key-1",
                    "metadata": {},
                }
            ],
            "queue": "priority-lane",
            "task_id": "job-1",
        }
    ]
    assert json.loads(redis.values[job_store.status_key("job-1")])["status"] == (
        "queued"
    )
    assert len(redis.streams[job_store.events_key("job-1")]) == 1
    provenance = json.loads(redis.values[job_store.provenance_key("job-1")])
    assert provenance["row_identity"] == {"field": "id"}


def test_create_area_job_dispatches_server_calculated_location_areas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = deepcopy(_manifest())
    column = manifest["metrics"][0]["output"]["columns"][0]
    column.update(
        {
            "name": "covered_area_m2",
            "type": "number",
            "unit": "m2",
            "derivations": [
                {
                    "kind": "fraction_of_location_area",
                    "name": "covered_area_fraction",
                    "description": "Fraction of the location covered.",
                }
            ],
        }
    )
    _use_repo(tmp_path, monkeypatch, manifest=manifest)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-area"))
    monkeypatch.setattr(
        job_submission,
        "calculate_feature_areas_m2",
        lambda _location: {"area-1": 123.0},
    )

    response = asyncio.run(
        jobs.create_job(
            JobCreateRequest(metric="heavy_metric", input=_spatial_payload())
        )
    )

    assert response.job_id == "job-area"
    assert celery.sent[0]["args"][0]["location_areas_m2"] == {"area-1": 123.0}


def test_create_area_job_rejects_location_without_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_location(_location: GeoJSON) -> dict[str, float]:
        error = "polygon required"
        raise ValueError(error)

    manifest = deepcopy(_manifest())
    column = manifest["metrics"][0]["output"]["columns"][0]
    column.update(
        {
            "name": "covered_area_m2",
            "type": "number",
            "unit": "m2",
            "derivations": [
                {
                    "kind": "fraction_of_location_area",
                    "name": "covered_area_fraction",
                    "description": "Fraction of the location covered.",
                }
            ],
        }
    )
    _use_repo(tmp_path, monkeypatch, manifest=manifest)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(
        job_submission,
        "calculate_feature_areas_m2",
        reject_location,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(metric="heavy_metric", input=_spatial_payload())
            )
        )

    assert exc_info.value.status_code == 422
    detail = cast("list[dict[str, Any]]", exc_info.value.detail)
    assert detail[0]["loc"] == ["location"]
    assert celery.sent == []


def test_canonical_request_fingerprint_ignores_nested_mapping_order() -> None:
    first = job_submission.canonical_request_fingerprint(
        "heavy_metric",
        {"outer": {"b": 2, "a": 1}, "value": 3},
    )
    second = job_submission.canonical_request_fingerprint(
        "heavy_metric",
        {"value": 3, "outer": {"a": 1, "b": 2}},
    )

    assert first == second
    assert first != job_submission.canonical_request_fingerprint(
        "other_metric",
        {"value": 3, "outer": {"a": 1, "b": 2}},
    )


def test_create_job_reuses_equivalent_idempotent_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    job_ids = iter(["job-1", "job-2"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(job_ids)),
    )
    request = JobCreateRequest(
        metric="heavy_metric",
        input=_spatial_payload(),
        idempotency_key="retry-key",
    )

    first = asyncio.run(jobs.create_job(request))
    replay = asyncio.run(jobs.create_job(request))

    assert first.job_id == replay.job_id == "job-1"
    assert first.reused is False
    assert replay.reused is True
    assert len(celery.sent) == 1
    assert len(redis.streams[job_store.events_key("job-1")]) == 1
    reservation_key = job_store.idempotency_key("retry-key")
    assert (reservation_key, job_store.JOB_STORE_TTL_SECONDS) in redis.expirations
    assert (
        job_store.job_idempotency_key("job-1"),
        job_store.JOB_STORE_TTL_SECONDS,
    ) in redis.expirations


def test_concurrent_equivalent_submissions_dispatch_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = ConcurrentFakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    job_ids = iter(["job-1", "job-2"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(job_ids)),
    )
    request = JobCreateRequest(
        metric="heavy_metric",
        input=_spatial_payload(),
        idempotency_key="concurrent-key",
    )

    async def submit_both() -> tuple[Any, Any]:
        first, second = await asyncio.gather(
            jobs.create_job(request),
            jobs.create_job(request),
        )
        return first, second

    first, second = asyncio.run(submit_both())

    assert first.job_id == second.job_id
    assert sorted([first.reused, second.reused]) == [False, True]
    assert len(celery.sent) == 1


def test_create_job_rejects_conflicting_idempotency_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    job_ids = iter(["job-1", "job-2"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(job_ids)),
    )
    asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input=_spatial_payload(),
                idempotency_key="conflict-key",
            )
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="heavy_metric",
                    input={**_spatial_payload(), "value": 4},
                    idempotency_key="conflict-key",
                )
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "idempotency_conflict",
        "message": "The idempotency key is already bound to a different request.",
        "idempotency_key": "conflict-key",
        "job_id": "job-1",
    }
    assert len(celery.sent) == 1


def test_submission_limit_exempts_replays_conflicts_and_rejection_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    get_config().agent_submission_limit.limit = 1
    get_config().agent_submission_limit.window_seconds = 23
    job_ids = iter(["job-1", "job-2", "job-3", "job-4"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(job_ids)),
    )
    request = JobCreateRequest(
        metric="heavy_metric",
        input=_spatial_payload(),
        idempotency_key="accepted-key",
    )

    accepted = asyncio.run(jobs.create_job(request))
    replay = asyncio.run(jobs.create_job(request))
    with pytest.raises(HTTPException) as conflict_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="heavy_metric",
                    input={**_spatial_payload(), "value": 4},
                    idempotency_key="accepted-key",
                )
            )
        )
    with pytest.raises(HTTPException) as limited_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="heavy_metric",
                    input=_spatial_payload(),
                    idempotency_key="rejected-key",
                )
            )
        )

    assert accepted.reused is False
    assert replay.reused is True
    assert conflict_info.value.status_code == 409
    assert limited_info.value.status_code == 429
    assert limited_info.value.headers == {"Retry-After": "23"}
    assert limited_info.value.detail == {
        "code": "rate_limited",
        "message": "Agent job submission limit exceeded. Please try again later.",
        "retry_after_seconds": 23,
    }
    assert redis.values[job_store.agent_submission_limit_key()] == "1"
    assert redis.values.get(job_store.idempotency_key("rejected-key")) is None
    assert redis.values.get(job_store.job_idempotency_key("job-4")) is None
    assert redis.values.get(job_store.status_key("job-4")) is None
    assert redis.values.get(job_store.provenance_key("job-4")) is None
    assert job_store.events_key("job-4") not in redis.streams
    assert len(celery.sent) == 1


def test_rest_and_mcp_submissions_share_one_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(job_submission, "redis_client", redis)
    celery_module = importlib.import_module("lyra_app.celery_app")
    monkeypatch.setattr(celery_module, "celery_app", celery)
    get_config().agent_submission_limit.limit = 2
    get_config().agent_submission_limit.window_seconds = 31
    rest_job_ids = iter(["rest-job-1", "rest-job-2"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(rest_job_ids)),
    )
    monkeypatch.setattr(job_submission, "_new_job_id", lambda: "mcp-job-1")

    rest_response = asyncio.run(
        jobs.create_job(
            JobCreateRequest(metric="heavy_metric", input=_spatial_payload())
        )
    )
    mcp_response = asyncio.run(
        InProcessLyraBackend().create_job(
            "heavy_metric",
            _spatial_payload(data_type="met_zone_code", value="09.01"),
        )
    )
    with pytest.raises(HTTPException) as limited_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(metric="heavy_metric", input=_spatial_payload())
            )
        )

    assert rest_response.job_id == "rest-job-1"
    assert mcp_response.job_id == "mcp-job-1"
    assert limited_info.value.status_code == 429
    assert limited_info.value.headers == {"Retry-After": "31"}
    assert redis.values[job_store.agent_submission_limit_key()] == "2"
    assert [task["task_id"] for task in celery.sent] == [
        "rest-job-1",
        "mcp-job-1",
    ]


def test_dispatch_failure_releases_idempotency_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FailOnceCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    job_ids = iter(["job-1", "job-2"])
    monkeypatch.setattr(
        jobs,
        "uuid4",
        lambda: SimpleNamespace(hex=next(job_ids)),
    )
    request = JobCreateRequest(
        metric="heavy_metric",
        input=_spatial_payload(),
        idempotency_key="recoverable-key",
    )

    with pytest.raises(RuntimeError, match="dispatch failed"):
        asyncio.run(jobs.create_job(request))
    recovered = asyncio.run(jobs.create_job(request))

    assert recovered.job_id == "job-2"
    assert recovered.reused is False
    assert len(celery.sent) == 1
    assert redis.values[job_store.agent_submission_limit_key()] == "1"


def test_create_job_rejects_unknown_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(JobCreateRequest(metric="missing", input={"value": 3}))
        )

    assert exc_info.value.status_code == 404


def test_create_job_rejects_invalid_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.create_job(JobCreateRequest(metric="heavy_metric", input={})))

    assert exc_info.value.status_code == 422


def test_create_job_rejects_duplicate_batch_keys_before_queueing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch, manifest=_batched_manifest())
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    monkeypatch.setattr(jobs, "celery_app", celery)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="batched_metric",
                    input={
                        "location": {
                            "data_type": "geojson",
                            "value": _feature_collection(),
                        },
                        "sector_filters": [
                            {"key": "retail", "value": "^46.*"},
                            {"key": "retail", "value": "^47.*"},
                        ],
                    },
                )
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == [
        {
            "loc": ["sector_filters"],
            "msg": "Batch input keys must be unique: retail.",
            "type": "unique_batch_keys",
        }
    ]
    assert celery.sent == []
    assert redis.values == {}
    assert redis.streams == {}


def test_create_job_rejects_raw_geojson_spatial_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="heavy_metric",
                    input={"location": _feature_collection(), "value": 3},
                )
            )
        )

    assert exc_info.value.status_code == 422


def test_create_job_resolves_cvegeo_list_spatial_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-1"))

    asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input=_spatial_payload(
                    data_type="cvegeo_list",
                    value=["090020001"],
                ),
            )
        )
    )

    dispatched_input = celery.sent[0]["args"][0]["input"]
    assert dispatched_input["location"] == _feature_collection("cvegeo-area")


def test_create_job_resolves_met_zone_code_spatial_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    celery = FakeCelery()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", celery)
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-1"))

    asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input=_spatial_payload(
                    data_type="met_zone_code",
                    value="09.01",
                ),
            )
        )
    )

    dispatched_input = celery.sent[0]["args"][0]["input"]
    assert dispatched_input["location"] == _feature_collection("met-09.01")
    provenance = json.loads(redis.values[job_store.provenance_key("job-1")])
    entry = registry.get_metric_entry("heavy_metric")
    assert entry is not None
    assert provenance == {
        "metric": "heavy_metric",
        "catalog_fingerprint": registry.get_public_catalog_fingerprint(),
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "input": _spatial_payload(data_type="met_zone_code", value="09.01"),
        "output": entry.metric.output.model_dump(mode="json"),
        "created_at": provenance["created_at"],
        "row_identity": {
            "field": "cvegeo",
            "namespace": "inegi:cvegeo:ageb",
            "version": "2020",
        },
    }
    assert "coordinates" not in json.dumps(provenance)


def test_stored_provenance_survives_catalog_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    _patch_converter_map(monkeypatch)
    monkeypatch.setattr(jobs, "celery_app", FakeCelery())
    monkeypatch.setattr(jobs, "uuid4", lambda: SimpleNamespace(hex="job-1"))

    asyncio.run(
        jobs.create_job(
            JobCreateRequest(
                metric="heavy_metric",
                input=_spatial_payload(
                    data_type="met_zone_code",
                    value="09.01",
                ),
            )
        )
    )
    stored = redis.values[job_store.provenance_key("job-1")]
    awaitable = job_store.set_job_status_async("job-1", "succeeded")
    asyncio.run(awaitable)
    redis.values[job_store.result_key("job-1")] = json.dumps(
        TableJobResult(
            job_id="job-1",
            index=["area-1"],
            columns=["value"],
            data=[[3]],
        ).model_dump(mode="json")
    )
    descriptor_before = asyncio.run(job_store.get_job_result_descriptor_async("job-1"))
    assert descriptor_before is not None
    old_entry = registry.get_metric_entry("heavy_metric")
    assert old_entry is not None

    changed_manifest = _manifest()
    changed_manifest["plugin"]["version"] = "2.0.0"
    changed_manifest["metrics"][0]["description"] = "A changed heavy metric."
    (tmp_path / "repo" / MANIFEST_FILENAME).write_text(
        json.dumps(changed_manifest),
        encoding="utf-8",
    )
    registry.refresh_catalog()

    new_entry = registry.get_metric_entry("heavy_metric")
    assert new_entry is not None
    assert new_entry.catalog_fingerprint != old_entry.catalog_fingerprint
    assert new_entry.plugin_version == "2.0.0"
    assert redis.values[job_store.provenance_key("job-1")] == stored
    descriptor_after = asyncio.run(job_store.get_job_result_descriptor_async("job-1"))
    assert descriptor_after is not None
    before_payload = descriptor_before.model_dump(mode="json")
    after_payload = descriptor_after.model_dump(mode="json")
    before_payload.pop("lifetime")
    after_payload.pop("lifetime")
    assert after_payload == before_payload


def test_create_job_rejects_invalid_cvegeo_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync())
    _patch_converter_map(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(
                    metric="heavy_metric",
                    input=_spatial_payload(data_type="cvegeo_list", value=["1"]),
                )
            )
        )

    assert exc_info.value.status_code == 422


def test_create_job_returns_503_when_spatial_resolution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync())

    def fail_resolution(geojson: GeoJSON) -> GeoJSON:  # noqa: ARG001
        raise SQLAlchemyError

    converter_map = {
        "location": {
            "geojson": fail_resolution,
            "cvegeo_list": fail_resolution,
            "met_zone_code": fail_resolution,
        },
        "bounds": {
            "geojson": fail_resolution,
            "cvegeo_list": fail_resolution,
            "met_zone_code": fail_resolution,
        },
    }
    monkeypatch.setitem(
        sys.modules,
        "lyra_app.converters",
        SimpleNamespace(converter_map=converter_map),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(
                JobCreateRequest(metric="heavy_metric", input=_spatial_payload())
            )
        )

    assert exc_info.value.status_code == 503


def test_create_job_returns_503_when_redis_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FakeRedisAsync(available=False))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(JobCreateRequest(metric="heavy_metric", input={"value": 3}))
        )

    assert exc_info.value.status_code == 503


def test_create_job_returns_503_when_redis_ping_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_repo(tmp_path, monkeypatch)
    _patch_redis(monkeypatch, FailingPingRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            jobs.create_job(JobCreateRequest(metric="heavy_metric", input={"value": 3}))
        )

    assert exc_info.value.status_code == 503


def test_get_job_returns_current_status(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(
        job_store.set_job_status_async("job-1", "started", metric="heavy_metric")
    )

    response = asyncio.run(jobs.get_job("job-1"))

    assert response.job_id == "job-1"
    assert response.metric == "heavy_metric"
    assert response.status == "started"


def test_get_job_returns_404_for_missing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.get_job("missing"))

    assert exc_info.value.status_code == 404


def test_job_events_stream_typed_sse_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", "queued", metric="metric"))
    first_id = redis.streams[job_store.events_key("job-1")][0][0]
    asyncio.run(
        job_store.set_job_status_async(
            "job-1",
            "succeeded",
            event_data={"job_id": "job-1", "status": "succeeded"},
        )
    )

    response = asyncio.run(
        jobs.get_job_events(
            "job-1",
            cast("Request", FakeRequest()),
            last_event_id=first_id,
        )
    )
    body = asyncio.run(_body(response))

    assert "id: 2-0\n" in body
    assert "event: succeeded\n" in body
    assert 'data: {"job_id":"job-1","event":"succeeded"' in body
    assert "event: queued\n" not in body


def test_job_events_stream_closes_after_terminal_last_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(
        job_store.set_job_status_async(
            "job-1",
            "succeeded",
            event_data={"job_id": "job-1", "status": "succeeded"},
        )
    )
    terminal_id = redis.streams[job_store.events_key("job-1")][0][0]

    response = asyncio.run(
        jobs.get_job_events(
            "job-1",
            cast("Request", FakeRequest()),
            last_event_id=terminal_id,
        )
    )

    assert asyncio.run(_body(response)) == ""


def test_job_result_returns_404_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.get_job_result("job-1"))

    assert exc_info.value.status_code == 404


def test_job_result_repairs_celery_failure_before_returning_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", "started"))
    failure = FailedJobResult(
        job_id="job-1",
        error={"type": "worker", "message": "worker disappeared"},
    )

    async def repair(
        snapshot: job_store.JobStatusSnapshot,
    ) -> job_store.JobStatusSnapshot:
        payload = failure.model_dump(mode="json", exclude_none=True)
        await redis.set(
            job_store.result_key(snapshot.job_id),
            json.dumps(payload),
            ex=600,
        )
        return await job_store.set_job_status_async(
            snapshot.job_id,
            "failed",
            error=failure.error,
        )

    monkeypatch.setattr(jobs, "reconcile_celery_failure", repair)

    response = asyncio.run(jobs.get_job_result("job-1"))

    assert json.loads(bytes(response.body)) == failure.model_dump(mode="json")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            TableJobResult(
                job_id="job-1",
                index=["area-1"],
                columns=["value"],
                data=[[6]],
            ),
            {
                "kind": "table",
                "job_id": "job-1",
                "status": "succeeded",
                "index": ["area-1"],
                "columns": ["value"],
                "data": [[6]],
            },
        ),
        (
            FailedJobResult(
                job_id="job-1",
                error={"type": "worker"},
            ),
            {
                "kind": "failed",
                "job_id": "job-1",
                "status": "failed",
                "error": {"type": "worker"},
            },
        ),
        (
            CancelledJobResult(job_id="job-1"),
            {
                "kind": "cancelled",
                "job_id": "job-1",
                "status": "cancelled",
            },
        ),
    ],
)
def test_job_result_returns_json_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
    result: TableJobResult | FailedJobResult | CancelledJobResult,
    expected: dict[str, Any],
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    redis.values[job_store.result_key("job-1")] = json.dumps(
        result.model_dump(mode="json", exclude_none=True)
    )

    response = asyncio.run(jobs.get_job_result("job-1"))

    assert isinstance(response, JSONResponse)
    assert json.loads(bytes(response.body)) == expected


def test_job_result_returns_file_metadata_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FileJobResult(
            job_id="job-1",
            file_path=str(output),
            media_type="image/tiff",
        ).model_dump(mode="json", exclude_none=True)
    )

    first_response = asyncio.run(jobs.get_job_result("job-1"))
    second_response = asyncio.run(jobs.get_job_result("job-1"))

    expected = {
        "kind": "file",
        "job_id": "job-1",
        "status": "succeeded",
        "file_path": str(output),
        "media_type": "image/tiff",
    }
    assert isinstance(first_response, JSONResponse)
    assert isinstance(second_response, JSONResponse)
    assert json.loads(bytes(first_response.body)) == expected
    assert json.loads(bytes(second_response.body)) == expected
    assert output.exists()
    assert redis.deleted == []


def test_job_result_descriptor_returns_table_metadata_and_jsonl_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", "succeeded"))
    redis.values[job_store.result_key("job-1")] = json.dumps(
        TableJobResult(
            job_id="job-1",
            index=["area-1", "area-2"],
            columns=["value"],
            data=[[6], [7]],
        ).model_dump(mode="json", exclude_none=True)
    )

    response = asyncio.run(jobs.get_job_result_descriptor("job-1"))

    assert isinstance(response, JSONResponse)
    content = json.loads(bytes(response.body))
    assert content["job_id"] == "job-1"
    assert content["schema_version"] == 1
    assert "completed_at" in content
    assert content["status"] == "succeeded"
    assert content["result_kind"] == "table"
    assert content["result_ref"] == "lyra://results/job-1"
    assert content["raw"] == {
        "result_ref": "lyra://results/job-1",
        "formats": ["terminal_json", "jsonl"],
        "terminal_json_path": "/jobs/job-1/result",
        "jsonl_path": "/jobs/job-1/result/table.jsonl",
    }
    assert content["table"] == {
        "row_count": 2,
        "column_count": 1,
        "columns": ["value"],
        "column_contracts": [],
        "index_field": "_result_index",
    }
    assert content["preview"]["rows"] == [
        {"_result_index": "area-1", "value": 6},
        {"_result_index": "area-2", "value": 7},
    ]


def test_job_result_descriptor_returns_file_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", "succeeded"))
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FileJobResult(
            job_id="job-1",
            file_path=str(output),
            media_type="image/tiff",
        ).model_dump(mode="json", exclude_none=True)
    )

    response = asyncio.run(jobs.get_job_result_descriptor("job-1"))

    assert isinstance(response, JSONResponse)
    content = json.loads(bytes(response.body))
    assert content["result_kind"] == "file"
    assert content["raw"] == {
        "result_ref": "lyra://results/job-1",
        "formats": ["terminal_json"],
        "terminal_json_path": "/jobs/job-1/result",
    }
    assert content["file"] == {
        "file_path": str(output),
        "media_type": "image/tiff",
    }
    assert "jsonl_path" not in content["raw"]


@pytest.mark.parametrize(
    ("result", "expected_kind", "expected_status", "expected_error"),
    [
        (
            FailedJobResult(job_id="job-1", error={"type": "worker"}),
            "failed",
            "failed",
            {"type": "worker"},
        ),
        (CancelledJobResult(job_id="job-1"), "cancelled", "cancelled", None),
    ],
)
def test_job_result_descriptor_returns_terminal_error_descriptors(
    monkeypatch: pytest.MonkeyPatch,
    result: FailedJobResult | CancelledJobResult,
    expected_kind: str,
    expected_status: str,
    expected_error: dict[str, Any] | None,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(job_store.set_job_status_async("job-1", result.status))
    redis.values[job_store.result_key("job-1")] = json.dumps(
        result.model_dump(mode="json", exclude_none=True)
    )

    response = asyncio.run(jobs.get_job_result_descriptor("job-1"))

    assert isinstance(response, JSONResponse)
    content = json.loads(bytes(response.body))
    assert content["result_ref"] == "lyra://results/job-1"
    assert content["result_kind"] == expected_kind
    assert content["status"] == expected_status
    assert content["summary"]["kind"] == expected_kind
    assert content["summary"].get("error") == expected_error
    assert content.get("error") == expected_error


def test_job_result_descriptor_returns_running_status_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    asyncio.run(
        job_store.set_job_status_async("job-1", "progress", metric="heavy_metric")
    )

    response = asyncio.run(jobs.get_job_result_descriptor("job-1"))

    assert response.status_code == 202
    content = json.loads(bytes(response.body))
    assert content["job_id"] == "job-1"
    assert content["metric"] == "heavy_metric"
    assert content["status"] == "progress"
    assert content["result_ref"] == "lyra://results/job-1"
    assert content["detail"] == "Result is not available yet"


def test_job_result_descriptor_returns_404_for_expired_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.get_job_result_descriptor("job-1"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Result expired or not found"


def test_job_result_jsonl_streams_table_rows_with_result_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    redis.values[job_store.result_key("job-1")] = json.dumps(
        TableJobResult(
            job_id="job-1",
            index=["area-1", "area-2"],
            columns=["_result_index", "value"],
            data=[["column-1", 6], ["column-2", 7]],
        ).model_dump(mode="json", exclude_none=True)
    )

    response = asyncio.run(jobs.export_job_result_jsonl("job-1"))

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "application/x-ndjson"
    assert response.headers["content-disposition"] == (
        'attachment; filename="job-1.jsonl"'
    )
    assert asyncio.run(_body(response)) == (
        '{"__result_index":"area-1","_result_index":"column-1","value":6}\n'
        '{"__result_index":"area-2","_result_index":"column-2","value":7}\n'
    )


def test_job_result_jsonl_returns_409_for_file_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FileJobResult(
            job_id="job-1",
            file_path=str(output),
            media_type="image/tiff",
        ).model_dump(mode="json", exclude_none=True)
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.export_job_result_jsonl("job-1"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Job result is not a table"


def test_job_result_jsonl_returns_404_for_expired_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.export_job_result_jsonl("job-1"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Result expired or not found"


def test_job_result_download_returns_file_bytes_repeatedly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    output = tmp_path / "result.tif"
    output.write_bytes(b"data")
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FileJobResult(
            job_id="job-1",
            file_path=str(output),
            media_type="image/tiff",
        ).model_dump(mode="json", exclude_none=True)
    )
    monkeypatch.setattr(jobs, "Path", FakeAsyncPath)

    first_response = asyncio.run(jobs.download_job_result("job-1"))
    second_response = asyncio.run(jobs.download_job_result("job-1"))

    assert isinstance(first_response, FileResponse)
    assert first_response.media_type == "image/tiff"
    assert asyncio.run(_file_response_body(first_response)) == b"data"
    assert asyncio.run(_file_response_body(second_response)) == b"data"
    assert output.exists()
    assert redis.deleted == []


def test_job_result_download_returns_404_when_file_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    output = tmp_path / "missing.tif"
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FileJobResult(
            job_id="job-1",
            file_path=str(output),
            media_type="image/tiff",
        ).model_dump(mode="json", exclude_none=True)
    )
    monkeypatch.setattr(jobs, "Path", FakeAsyncPath)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(jobs.download_job_result("job-1"))

    assert exc_info.value.status_code == 404
