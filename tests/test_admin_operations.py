from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from redis.exceptions import RedisError

from lyra_app import job_store
from lyra_app.config import clear_config_cache
from lyra_app.registry import reset_catalog
from lyra_app.routes import admin
from tests.config_helpers import load_test_config
from tests.redis_job_scripts import eval_job_script, seed_status

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> None:
        if nx and key in self.values:
            return
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(
            self.sorted_sets.get(key, {}),
            key=lambda member: self.sorted_sets[key][member],
            reverse=True,
        )
        return members[start : stop + 1]

    def zrem(self, key: str, *members: str) -> None:
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member in members:
            sorted_set.pop(member, None)

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | float,
    ) -> int | str:
        return eval_job_script(self, numkeys, keys_and_args, script)


class FailingRedisSync(FakeRedisSync):
    def zrevrange(self, *_args: object, **_kwargs: object) -> list[str]:
        assert isinstance(self, FailingRedisSync)
        raise RedisError


@pytest.fixture
def admin_context(
    tmp_path: Path,
) -> Iterator[Path]:
    reset_catalog()
    load_test_config(tmp_path)
    yield tmp_path
    reset_catalog()
    clear_config_cache()


def _assert_http_error(
    status_code: int,
    func: Callable[..., object],
    *args: object,
) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        func(*args)
    assert exc_info.value.status_code == status_code
    return exc_info.value


def test_require_admin_key_reads_configured_secret_file(tmp_path: Path) -> None:
    load_test_config(tmp_path)

    try:
        admin.require_admin_key(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-secret")
        )

        with pytest.raises(HTTPException) as exc_info:
            admin.require_admin_key(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            )

        assert exc_info.value.status_code == 403
    finally:
        clear_config_cache()


def test_admin_router_requires_bearer_key_for_all_routes() -> None:
    assert admin.router.prefix == "/admin"
    assert any(
        dependency.dependency is admin.require_admin_key
        for dependency in admin.router.dependencies
    )


def test_admin_jobs_list_returns_empty_response(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FakeRedisSync())

    response = admin.list_jobs()

    assert response.model_dump() == {"jobs": []}


def test_admin_jobs_list_filters_by_status_and_metric(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    seed_status("job-1", "queued", metric="heavy_metric", client=redis)
    seed_status("job-2", "queued", metric="heavy_metric", client=redis)
    seed_status("job-2", "running", metric="heavy_metric", client=redis)
    seed_status("job-3", "queued", metric="light_metric", client=redis)
    seed_status("job-3", "running", metric="light_metric", client=redis)

    response = admin.list_jobs(limit=10, status="running", metric="heavy_metric")

    assert [job.job_id for job in response.jobs] == ["job-2"]


def test_admin_jobs_list_returns_503_when_redis_is_unavailable(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FailingRedisSync())

    failed = _assert_http_error(503, admin.list_jobs)

    assert "Cannot connect to Redis" in str(failed.detail)


def test_admin_jobs_limit_validation_is_documented_in_openapi() -> None:
    app = FastAPI()
    app.include_router(admin.router)
    operation = app.openapi()["paths"]["/admin/jobs"]["get"]

    limit_parameter = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "limit"
    )

    assert limit_parameter["schema"]["minimum"] == 1
    assert limit_parameter["schema"]["maximum"] == 100


def test_admin_cancel_job_marks_active_job_and_revokes_task(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    revoked: list[str] = []
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    monkeypatch.setattr(admin, "revoke_job", revoked.append)
    seed_status("job-1", "queued", metric="heavy_metric", client=redis)
    seed_status("job-1", "running", metric="heavy_metric", client=redis)

    response = admin.cancel_job("job-1")

    assert response.model_dump() == {
        "job_id": "job-1",
        "status": "cancelled",
        "cancellation_requested": True,
        "revoke_requested": True,
    }
    stored_snapshot = job_store.get_job_status("job-1", client=redis)
    assert stored_snapshot is not None
    assert stored_snapshot.status == "cancelled"
    result = job_store.get_job_result("job-1", client=redis)
    assert result is not None
    assert result["status"] == "cancelled"
    assert revoked == ["job-1"]


def test_admin_cancel_job_rejects_terminal_job(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedisSync()
    revoked: list[str] = []
    monkeypatch.setattr(admin.job_store, "redis_client_sync", redis)
    monkeypatch.setattr(admin, "revoke_job", revoked.append)
    seed_status("job-1", "queued", metric="heavy_metric", client=redis)
    seed_status("job-1", "succeeded", metric="heavy_metric", client=redis)

    failed = _assert_http_error(409, admin.cancel_job, "job-1")

    assert "already terminal" in str(failed.detail)
    stored_snapshot = job_store.get_job_status("job-1", client=redis)
    assert stored_snapshot is not None
    assert stored_snapshot.status == "succeeded"
    assert revoked == []


def test_admin_cancel_job_returns_404_for_unknown_job(
    admin_context: Path,  # ruff:ignore[unused-function-argument]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin.job_store, "redis_client_sync", FakeRedisSync())

    failed = _assert_http_error(404, admin.cancel_job, "missing")

    assert "Job expired or not found" in str(failed.detail)
