import asyncio
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from lyra.sdk.models import FailedJobResult, FileJobResult, JobCreateRequest
from lyra.sdk.models.geometry import GeoJSON
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from lyra_app import job_store, registry
from lyra_app.config import clear_config_cache
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import jobs
from tests.config_helpers import load_test_config


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
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
                "entrypoint": "fake_plugin.runner:run",
            }
        ],
    }


def _batched_manifest() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
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
                "entrypoint": "fake_plugin.runner:run",
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
    value: Any | None = None,
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
        self.deleted: list[str] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    async def ping(self) -> bool:
        return self.available

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    async def xrange(
        self,
        key: str,
        *,
        min: str,  # noqa: A002
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        records = self.streams.get(key, [])
        if min.startswith("("):
            after_id = min[1:]
            records = [record for record in records if record[0] > after_id]
        elif min != job_store.STREAM_START:
            records = [record for record in records if record[0] >= min]
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
    async def run_inline(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(jobs.asyncio, "to_thread", run_inline)
    registry.reset_catalog()
    load_test_config(
        tmp_path,
        metric_queues={
            "batched_metric": "priority-lane",
            "heavy_metric": "priority-lane",
        },
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
        registry, "sync_catalog_repos", lambda _config: [_synced_repo(repo)]
    )
    registry.refresh_catalog()


def _patch_redis(monkeypatch: pytest.MonkeyPatch, redis: FakeRedisAsync) -> None:
    monkeypatch.setattr(jobs, "redis_client", redis)
    monkeypatch.setattr(jobs.job_store, "redis_client", redis)


async def _body(response: StreamingResponse) -> str:
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        async for chunk in response.body_iterator
    ]
    return "".join(chunks)


def test_create_job_dispatches_generic_task_to_toml_queue(
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


def test_create_job_rejects_unknown_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch, FakeRedisAsync())

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
        asyncio.run(jobs.get_job_result("job-1", BackgroundTasks()))

    assert exc_info.value.status_code == 404


def test_job_result_returns_json_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisAsync()
    _patch_redis(monkeypatch, redis)
    redis.values[job_store.result_key("job-1")] = json.dumps(
        FailedJobResult(
            job_id="job-1",
            error={"type": "worker"},
        ).model_dump(
            mode="json",
        )
    )

    response = asyncio.run(jobs.get_job_result("job-1", BackgroundTasks()))

    assert isinstance(response, JSONResponse)
    assert json.loads(bytes(response.body)) == {
        "kind": "failed",
        "job_id": "job-1",
        "status": "failed",
        "error": {"type": "worker"},
    }


def test_job_result_returns_file_and_cleans_result(
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
    background_tasks = BackgroundTasks()
    monkeypatch.setattr(jobs, "Path", FakeAsyncPath)

    response = asyncio.run(jobs.get_job_result("job-1", background_tasks))
    asyncio.run(background_tasks())

    assert isinstance(response, FileResponse)
    assert not output.exists()
    assert redis.deleted == [job_store.result_key("job-1")]
