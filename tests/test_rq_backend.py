"""Production-facing real Redis integration tests for the reduced job contract."""

import asyncio
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lyra.sdk.models.geometry import GeoJSON
from lyra.sdk.models.job import (
    AdminJobDetail,
    FailedJobResult,
    JobStatusInfo,
    TableJobResult,
)
from lyra.sdk.types import JsonObject
from redis.client import Pipeline
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Worker
from rq.job import Job
from rq.results import Result
from rq.serializers import JSONSerializer

from lyra_app import job_store, job_submission
from lyra_app.config import get_config
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.db.redis import get_sync_client
from lyra_app.main import lifespan, redis_unavailable_response
from lyra_app.mcp.results import project_observation
from lyra_app.mcp.serialization import serialize_result
from lyra_app.registry import MetricPayloadValidationError
from lyra_app.routes import admin, jobs
from lyra_app.worker import WorkerRunContext
from tests.rq_helpers import Backend, eventually, pool
from tests.smoke_plugin_helpers import feature_collection


def snapshot(job_id: str) -> JobStatusInfo:
    value = job_store.read_job(job_id).snapshot
    assert value is not None
    return value


def detail(job_id: str) -> AdminJobDetail:
    value = job_store.get_job_detail(job_id)
    assert value is not None
    return value


def test_independent_submissions_validation_and_contract_capture(
    backend: Backend,
) -> None:
    first, second = backend.submit(), backend.submit()
    assert first.job_id != second.job_id
    assert backend.queue.job_ids == [first.job_id, second.job_id]
    with pytest.raises(MetricPayloadValidationError):
        backend.submit(parameters={"seconds": -1})
    assert backend.queue.count == 2
    native = backend.queue.fetch_job(first.job_id)
    assert native is not None
    assert native.timeout == 18000
    assert native.ttl is None
    assert native.result_ttl == native.failure_ttl == 86400
    assert not native.retries_left
    assert native.serializer is JSONSerializer
    assert native.meta["provenance"]["input"]["location"]["data_type"] == "geojson"
    assert "data_type" not in native.args[0]["input"]["location"]
    with pool(backend):
        eventually(
            lambda: backend.terminal(first.job_id) and backend.terminal(second.job_id)
        )
    result = job_store.read_job(first.job_id)
    assert isinstance(result.result, TableJobResult)
    assert result.result.data[0][1] == pytest.approx(0.25)
    assert result.provenance is not None
    assert (
        result.provenance.catalog_fingerprint
        == native.meta["provenance"]["catalog_fingerprint"]
    )


@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        ("crash", "worker"),
        ("invalid", "invalid_result"),
        ("database", "database_unavailable"),
    ],
)
def test_native_failure_and_safe_structured_error(
    backend: Backend, outcome: str, error_type: str
) -> None:
    submitted = backend.submit(parameters={"outcome": outcome})
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    observation = job_store.read_job(submitted.job_id)
    assert isinstance(observation.result, FailedJobResult)
    assert observation.result.error["type"] == error_type
    assert observation.snapshot is not None
    assert observation.snapshot.status == "failed"
    assert "Private diagnostic" not in observation.result.model_dump_json()
    detail = job_store.get_job_detail(submitted.job_id)
    assert detail is not None
    assert detail.failure_diagnostics
    native = backend.queue.fetch_job(submitted.job_id)
    assert native is not None
    native.meta.pop("failure", None)
    native.save_meta()
    assert job_store.read_job(submitted.job_id).result is not None
    assert snapshot(submitted.job_id).status == "failed"


def test_contract_mismatch_never_invokes_metric(backend: Backend) -> None:
    submitted = backend.submit()
    native = backend.queue.fetch_job(submitted.job_id)
    assert native is not None
    native.meta["contract"]["description"] = "Drift"
    native.save_meta()
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    assert snapshot(submitted.job_id).status == "failed"
    assert not (backend.scratch / submitted.job_id / "pid").exists()


def _app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.database = ApplicationDatabaseRuntime(get_config())
    app.include_router(jobs.router)
    app.include_router(admin.router)
    app.add_exception_handler(RedisConnectionError, redis_unavailable_response)
    return app


def test_authentication_repeated_downloads_retention_and_mcp(backend: Backend) -> None:
    backend.config.jobs.result_retention_seconds = 10
    submitted = backend.submit("feature_report")
    path = f"/jobs/{submitted.job_id}"
    headers = {"Authorization": "Bearer agent-secret"}
    with TestClient(_app()) as client:
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"Authorization": "Bearer wrong"}).status_code
            == 403
        )
        assert (
            client.get(path + "/result/descriptor", headers=headers).status_code == 202
        )
        with pool(backend):
            eventually(lambda: backend.terminal(submitted.job_id))
            observation = job_store.read_job(submitted.job_id)
            assert observation.result is not None
            descriptor = client.get(path + "/result/descriptor", headers=headers)
            assert descriptor.status_code == 200
            assert descriptor.json()["schema_version"] == 2
            first = client.get(path + "/result/download", headers=headers)
            assert first.status_code == 200
            assert (
                client.get(path + "/result/download", headers=headers).content
                == first.content
            )
            assert serialize_result(
                project_observation(
                    submitted.job_id, observation, "http://localhost"
                ).model_dump(mode="json")
            )
            assert (
                client.get(
                    f"/admin/jobs/{submitted.job_id}", headers=headers
                ).status_code
                == 403
            )
        artifact = backend.scratch / submitted.job_id / "features.txt"
        assert artifact.is_file()
        eventually(
            lambda: job_store.read_job(submitted.job_id).snapshot is None, seconds=15
        )
        assert client.get(path + "/result/download", headers=headers).status_code == 404
        assert artifact.is_file()


def test_pagination_skips_stale_entries_without_cleanup(backend: Backend) -> None:
    ids = [backend.submit().job_id for _ in range(3)]
    page = job_store.list_job_statuses(
        queue=backend.queue.name, status="queued", offset=1, limit=1
    )
    assert [item.job_id for item in page] == ids[1:2]
    backend.queue.connection.delete(Job.key_for(ids[1]))
    assert (
        job_store.list_job_statuses(
            queue=backend.queue.name, status="queued", offset=1, limit=1
        )
        == []
    )
    assert backend.queue.job_ids == ids
    with TestClient(_app()) as client:
        headers = {"Authorization": "Bearer admin-secret"}
        assert client.get("/admin/jobs", headers=headers).status_code == 422
        assert (
            client.get(
                "/admin/jobs",
                params={"queue": "unconfigured", "status": "queued"},
                headers=headers,
            ).status_code
            == 422
        )


def test_progress_is_best_effort_and_throttled(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = Mock()
    native.meta = {}
    native.save_meta.side_effect = RedisConnectionError("offline")
    monkeypatch.setattr("lyra_app.worker.get_current_job", lambda: native)
    context = WorkerRunContext(
        job_id="test", metric="work", logger=Mock(), temp_dir=backend.scratch, db=Mock()
    )
    context.report_progress(stage="work", current=1, total=2)
    context.report_progress(stage="work", current=2, total=2)
    context.flush_progress()
    assert native.save_meta.call_count == 1
    with pytest.raises(ValueError, match="current"):
        context.report_progress(stage="work", current=float("nan"))


def test_submission_and_observation_outages_are_explicit(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue = Mock(side_effect=RedisConnectionError("ambiguous"))
    monkeypatch.setattr(job_store, "enqueue", enqueue)
    with pytest.raises(job_submission.SubmissionUnavailableError, match="duplicate"):
        backend.submit()
    assert enqueue.call_count == 1
    connection = Mock()
    connection.hgetall.side_effect = RedisConnectionError("offline")
    monkeypatch.setattr(job_store, "get_sync_client", lambda: connection)
    with pytest.raises(RedisConnectionError):
        asyncio.run(job_store.observe_job("missing"))


@pytest.mark.parametrize("concurrency", [1, 5, 10])
def test_native_concurrency_and_graceful_shutdown(
    backend: Backend, concurrency: int
) -> None:
    backend.config.workers["test"].concurrency = concurrency
    ids = [
        backend.submit(parameters={"seconds": 10}).job_id for _ in range(concurrency)
    ]
    with pool(backend) as process:
        eventually(
            lambda: all((backend.scratch / job_id / "pid").exists() for job_id in ids)
        )
        pids = {int((backend.scratch / job_id / "pid").read_text()) for job_id in ids}
        assert len(pids) == concurrency
        assert os.getpid() not in pids
        pending = backend.submit().job_id
        process.terminate()
        process.join(timeout=15)
        assert process.exitcode == 0
        assert all(backend.terminal(job_id) for job_id in ids)
        assert snapshot(pending).status == "queued"


def test_child_loss_and_native_pool_replacement(backend: Backend) -> None:
    submitted = backend.submit(parameters={"seconds": 300})
    marker = backend.scratch / submitted.job_id / "pid"
    with pool(backend):
        eventually(marker.exists)
        os.kill(int(marker.read_text()), signal.SIGKILL)
        eventually(lambda: backend.terminal(submitted.job_id))
        occupied = backend.submit(parameters={"seconds": 300}).job_id
        occupied_marker = backend.scratch / occupied / "pid"
        eventually(occupied_marker.exists)
        first_worker = detail(occupied).worker_id
        workers = Worker.all(queue=backend.queue, serializer=JSONSerializer)
        victim = next(worker for worker in workers if worker.name == first_worker)
        assert victim.pid is not None
        os.kill(victim.pid, signal.SIGKILL)
        os.kill(int(occupied_marker.read_text()), signal.SIGKILL)
        eventually(
            lambda: any(
                worker.name != first_worker
                for worker in Worker.all(queue=backend.queue, serializer=JSONSerializer)
            )
        )
        replacement = backend.submit().job_id
        eventually(lambda: backend.terminal(replacement), seconds=40)
        assert detail(replacement).worker_id != first_worker
        assert snapshot(replacement).status == "succeeded"
    assert snapshot(submitted.job_id).status == "failed"


def test_whole_worker_loss_recovered_within_two_minutes(backend: Backend) -> None:
    backend.config.workers["test"].concurrency = 2
    submitted = backend.submit(parameters={"seconds": 300})
    marker = backend.scratch / submitted.job_id / "pid"
    with pool(backend):
        eventually(marker.exists)
        native = backend.queue.fetch_job(submitted.job_id)
        assert native is not None
        assert native.timeout == 18000
        workers = Worker.all(queue=backend.queue, serializer=JSONSerializer)
        victim = next(worker for worker in workers if worker.name == native.worker_name)
        started = time.monotonic()
        assert victim.pid is not None
        os.kill(victim.pid, signal.SIGKILL)
        os.kill(int(marker.read_text()), signal.SIGKILL)
        eventually(lambda: backend.terminal(submitted.job_id), seconds=120)
        assert time.monotonic() - started < 120
        assert snapshot(submitted.job_id).status == "failed"


def test_silent_work_heartbeats_and_native_timeout(backend: Backend) -> None:
    backend.config.jobs.execution_timeout_seconds = 9
    submitted = backend.submit(parameters={"seconds": 30})
    marker = backend.scratch / submitted.job_id / "pid"
    with pool(backend):
        eventually(marker.exists)
        native = backend.queue.fetch_job(submitted.job_id)
        assert native is not None
        first_heartbeat = native.last_heartbeat
        eventually(
            lambda: (
                native.refresh() is None and native.last_heartbeat != first_heartbeat
            ),
            seconds=8,
        )
        assert not backend.terminal(submitted.job_id)
        eventually(lambda: backend.terminal(submitted.job_id))
    assert "JobTimeoutException" in (detail(submitted.job_id).failure_diagnostics or "")


@pytest.mark.parametrize(
    ("metric", "parameters", "expected"),
    [
        ("capacity", {"units": 7}, [[7]]),
        ("reused_capacity", {}, [[4]]),
        (
            "selection_size",
            {"selection": {"activity_codes": ["a", "b"]}, "threshold": None},
            [[2]],
        ),
        ("interval_width", {"lower": 2, "upper": 9}, [[7]]),
    ],
)
def test_domain_contracts_through_production_worker(
    backend: Backend,
    metric: str,
    parameters: JsonObject,
    expected: list[list[int]],
) -> None:
    submitted = backend.submit(metric, parameters)
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    observed = job_store.read_job(submitted.job_id)
    assert isinstance(observed.result, TableJobResult)
    assert observed.result.data == expected
    assert observed.provenance is not None
    assert observed.provenance.input["parameters"] == parameters


def test_semantic_input_failure_is_retained(backend: Backend) -> None:
    submitted = backend.submit("interval_width", {"lower": 9, "upper": 2})
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    observed = job_store.read_job(submitted.job_id)
    assert isinstance(observed.result, FailedJobResult)
    assert observed.result.error["type"] == "invalid_input"


def test_spatial_resolution_and_captured_provenance(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:

    calls = []

    def resolve(code: str, *, engine: object) -> GeoJSON:
        calls.append((code, engine))
        return GeoJSON.model_validate(feature_collection())

    monkeypatch.setattr(
        "lyra_app.converters.map.load_location_from_met_zone_code", resolve
    )
    payload: JsonObject = {
        "location": {"data_type": "met_zone_code", "value": "09.01"},
        "parameters": {"units": 8},
    }
    submitted = backend.submit("capacity", payload=payload)
    assert len(calls) == 1
    assert calls[0][0] == "09.01"
    captured = job_store.read_job(submitted.job_id).provenance
    assert captured is not None
    assert captured.input == payload
    assert captured.row_identity is not None
    assert captured.row_identity.field == "cvegeo"
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    assert job_store.read_job(submitted.job_id).provenance == captured


def test_progress_write_failure_does_not_fail_computation(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Job.save_meta

    def fail_progress(job: Job) -> None:
        if "progress" in job.meta:
            raise RedisConnectionError
        original(job)

    monkeypatch.setattr(Job, "save_meta", fail_progress)
    submitted = backend.submit()
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    assert snapshot(submitted.job_id).status == "succeeded"


def test_missing_structured_failure_metadata_preserves_native_failure(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Job.save_meta

    def fail_error_metadata(job: Job) -> None:
        if "failure" in job.meta:
            raise RedisConnectionError
        original(job)

    monkeypatch.setattr(Job, "save_meta", fail_error_metadata)
    submitted = backend.submit(parameters={"outcome": "crash"})
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    assert snapshot(submitted.job_id).status == "failed"
    assert snapshot(submitted.job_id).error == {
        "type": "worker",
        "message": "Job execution failed.",
    }


def test_result_persistence_interruption_never_replays_execution(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:

    original = Result.save

    def fail_success(
        result: Result, ttl: int, pipeline: Pipeline | None = None
    ) -> str | None:
        if result.type == Result.Type.SUCCESSFUL:
            raise RedisConnectionError
        return original(result, ttl=ttl, pipeline=pipeline)

    monkeypatch.setattr(Result, "save", fail_success)
    submitted = backend.submit()
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    assert snapshot(submitted.job_id).status == "failed"
    native = backend.queue.fetch_job(submitted.job_id)
    assert native is not None
    assert not native.retries_left
    assert backend.queue.count == 0


def test_observation_expiration_between_reads_does_not_recreate_job(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = backend.submit()
    with pool(backend):
        eventually(lambda: backend.terminal(submitted.job_id))
    original = Job.latest_result

    def expire(job: Job) -> Result | None:
        result = original(job)
        backend.queue.connection.delete(job.key)
        return result

    monkeypatch.setattr(Job, "latest_result", expire)
    assert job_store.read_job(submitted.job_id).snapshot is None
    assert not backend.queue.connection.exists(Job.key_for(submitted.job_id))


def test_admin_worker_identity_and_freshness(backend: Backend) -> None:

    with pool(backend):
        eventually(
            lambda: len(Worker.all(queue=backend.queue, serializer=JSONSerializer)) > 0
        )
        response = admin.list_workers()
        observed = next(
            worker for worker in response.workers if backend.queue.name in worker.queues
        )
        assert observed.name != "test"
        assert observed.hostname
        assert observed.stale is False
        assert response.pools[0].name == "test"
        stale = datetime.now(UTC) - timedelta(seconds=40)
        worker = next(
            worker
            for worker in Worker.all(queue=backend.queue, serializer=JSONSerializer)
            if worker.name == observed.name
        )
        backend.queue.connection.hset(
            worker.key, "last_heartbeat", stale.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
        assert admin.get_worker(observed.name).stale is True


def test_authenticated_rest_submission_validates_before_enqueue(
    backend: Backend,
) -> None:
    payload = {
        "metric": "capacity",
        "input": {
            "location": {"data_type": "geojson", "value": feature_collection()},
            "parameters": {"units": 7},
        },
    }
    headers = {"Authorization": "Bearer agent-secret"}
    with TestClient(_app()) as client:
        assert client.post("/jobs", json=payload).status_code == 401
        assert (
            client.post(
                "/jobs", headers=headers, json={**payload, "idempotency_key": "removed"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/jobs", headers=headers, json={**payload, "factory": "os:system"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/jobs", headers=headers, json={**payload, "metric": "missing"}
            ).status_code
            == 404
        )
        assert backend.queue.count == 0
        first = client.post("/jobs", headers=headers, json=payload)
        second = client.post("/jobs", headers=headers, json=payload)
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] != second.json()["job_id"]
        assert "reused" not in first.json()
        assert backend.queue.count == 2


def test_admin_reads_do_not_clean_worker_registry_or_change_timeouts(
    backend: Backend,
) -> None:
    stale_key = f"rq:worker:{backend.queue.name}-missing"
    connection = backend.queue.connection
    connection.sadd(Worker.redis_workers_keys, stale_key)
    try:
        before = dict(get_sync_client().connection_pool.connection_kwargs)
        admin.list_workers()
        assert connection.sismember(Worker.redis_workers_keys, stale_key)
        assert get_sync_client().connection_pool.connection_kwargs == before
    finally:
        connection.srem(Worker.redis_workers_keys, stale_key)
