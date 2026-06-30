import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import JobEnvelope, TableJobResult
from lyra.sdk.models.plugin_v3 import TableOutputV3

from lyra_app import registry
from lyra_app.config import clear_config_cache
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from tests.config_helpers import load_test_config


def _v3_batched_manifest() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "light_metric",
                "description": "A lightweight metric.",
                "entrypoint": "fake_plugin.runner:run",
                "inputs": {
                    "location": {"kind": "location"},
                    "sector_filters": {
                        "kind": "batch",
                        "max_items": 20,
                        "value": {
                            "kind": "string",
                            "min_length": 1,
                            "max_length": 128,
                        },
                        "label": True,
                    },
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
                        }
                    ],
                },
            }
        ],
    }


def _table_output(
    *,
    columns: list[dict[str, Any]] | None = None,
    name: str = "job_accessibility_{key}",
    description: str = "Job accessibility for {label}.",
) -> TableOutputV3:
    return TableOutputV3.model_validate(
        {
            "kind": "table",
            "columns": [] if columns is None else columns,
            "batched_columns": [
                {
                    "source": "sector_filters",
                    "name": name,
                    "type": "number",
                    "unit": "jobs",
                    "description": description,
                }
            ],
        }
    )


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


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id


@pytest.fixture(autouse=True)
def reset_catalog(tmp_path: Path) -> Iterator[None]:
    registry.reset_catalog()
    load_test_config(tmp_path, metric_queues={"light_metric": "lightweight"})
    yield
    registry.reset_catalog()
    clear_config_cache()


@pytest.fixture
def worker_module(tmp_path: Path) -> Any:
    worker = importlib.import_module("lyra_app.worker")
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(tmp_path / "runner-temp")
    yield worker
    worker.RUNNER_REGISTRY.clear()
    worker.set_runner_temp_base(None)


def _decode_stored_result(
    worker: Any,
    redis: FakeRedisSync,
    job_id: str,
) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.result_key(job_id)])


def test_catalog_refresh_preserves_batched_column_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _v3_batched_manifest())
    monkeypatch.setattr(
        registry, "sync_catalog_repos", lambda _config: [_synced_repo(repo)]
    )

    registry.refresh_catalog()
    info = registry.get_metric_info("light_metric")

    assert info is not None
    info_payload = info.model_dump()
    assert info_payload["output"]["columns"] == []
    assert info_payload["output"]["batched_columns"][0]["source"] == "sector_filters"
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]


def test_worker_expands_batched_column_descriptions_from_label_or_key(
    worker_module: Any,
) -> None:
    output = _table_output(
        description="Job accessibility for {label} ({key}).",
    )

    expand_table_output_columns = worker_module.__dict__["_expand_table_output_columns"]
    columns = expand_table_output_columns(
        output,
        {
            "sector_filters": [
                {
                    "key": "sectors_091_092",
                    "value": "^09[12].*",
                    "label": "Sectors 091 and 092",
                },
                {"key": "retail", "value": "^46.*"},
            ]
        },
    )

    assert [column.name for column in columns] == [
        "job_accessibility_sectors_091_092",
        "job_accessibility_retail",
    ]
    assert [column.description for column in columns] == [
        "Job accessibility for Sectors 091 and 092 (sectors_091_092).",
        "Job accessibility for retail (retail).",
    ]


def test_batched_table_result_expands_columns_from_key_order(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=[
                f"job_accessibility_{sector_filter['key']}"
                for sector_filter in job.input["sector_filters"]
            ],
            data=[[1.5, 2.5]],
        )

    worker_module.RUNNER_REGISTRY["batched_metric"] = worker_module.RunnerMetricEntry(
        metric_name="batched_metric",
        queue="heavy",
        entrypoint="batched_plugin:run",
        output=_table_output(),
        run=run,
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-batched",
            "metric": "batched_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [
                    {
                        "key": "sectors_091_092",
                        "value": "^09[12].*",
                        "label": "Sectors 091 and 092",
                    },
                    {"key": "retail", "value": "^46.*"},
                ],
            },
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "table",
        "job_id": "job-batched",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": [
            "job_accessibility_sectors_091_092",
            "job_accessibility_retail",
        ],
        "data": [[1.5, 2.5]],
    }
    assert _decode_stored_result(worker_module, fake_redis, "job-batched") == result


def test_mixed_static_and_batched_table_result_persists_success(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["total_jobs", "job_accessibility_retail"],
            data=[[100, 3.5]],
        )

    worker_module.RUNNER_REGISTRY["mixed_batched_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="mixed_batched_metric",
            queue="heavy",
            entrypoint="mixed_batched_plugin:run",
            output=_table_output(
                columns=[
                    {
                        "name": "total_jobs",
                        "type": "integer",
                        "unit": "jobs",
                        "description": "Total jobs across all filters.",
                    }
                ],
            ),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-mixed-batched",
            "metric": "mixed_batched_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [{"key": "retail", "value": "^46.*"}],
            },
        },
        task_id="task-id",
    )

    assert result["status"] == "succeeded"
    assert result["columns"] == ["total_jobs", "job_accessibility_retail"]
    assert result["data"] == [[100, 3.5]]


@pytest.mark.parametrize(
    ("columns", "data"),
    [
        (["job_accessibility_sectors_091_092"], [[1.0]]),
        (
            [
                "job_accessibility_sectors_091_092",
                "job_accessibility_retail",
                "extra",
            ],
            [[1.0, 2.0, 3.0]],
        ),
        (
            ["job_accessibility_retail", "job_accessibility_sectors_091_092"],
            [[2.0, 1.0]],
        ),
    ],
)
def test_invalid_batched_table_columns_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
    columns: list[str],
    data: list[list[float]],
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=columns,
            data=data,
        )

    worker_module.RUNNER_REGISTRY["invalid_batched_columns_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_batched_columns_metric",
            queue="heavy",
            entrypoint="invalid_batched_columns_plugin:run",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-batched-columns",
            "metric": "invalid_batched_columns_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [
                    {"key": "sectors_091_092", "value": "^09[12].*"},
                    {"key": "retail", "value": "^46.*"},
                ],
            },
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert (
        _decode_stored_result(
            worker_module,
            fake_redis,
            "job-invalid-batched-columns",
        )
        == result
    )


@pytest.mark.parametrize("value", ["wrong", None])
def test_invalid_batched_table_values_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
    value: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["job_accessibility_retail"],
            data=[[value]],
        )

    worker_module.RUNNER_REGISTRY["invalid_batched_value_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_batched_value_metric",
            queue="heavy",
            entrypoint="invalid_batched_value_plugin:run",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-batched-value",
            "metric": "invalid_batched_value_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [{"key": "retail", "value": "^46.*"}],
            },
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert (
        _decode_stored_result(
            worker_module,
            fake_redis,
            "job-invalid-batched-value",
        )
        == result
    )


@pytest.mark.parametrize(
    ("source_value", "match"),
    [
        ({"key": "bad-key", "value": "^46.*"}, "key"),
        ({"key": "retail", "label": "Retail"}, "value"),
        ({"key": "retail", "value": "^46.*", "label": 123}, "label"),
        ({"key": "retail", "value": "^46.*", "pattern": "^46.*"}, "unsupported"),
    ],
)
def test_invalid_batched_source_values_persist_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
    source_value: dict[str, Any],
    match: str,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["job_accessibility_retail"],
            data=[[1.0]],
        )

    worker_module.RUNNER_REGISTRY["invalid_batched_source_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="invalid_batched_source_metric",
            queue="heavy",
            entrypoint="invalid_batched_source_plugin:run",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-invalid-batched-source",
            "metric": "invalid_batched_source_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [source_value],
            },
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"]["type"] == "invalid_result"
    assert match in result["error"]["message"]


def test_batched_table_generated_column_collision_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["job_accessibility_retail"],
            data=[[1]],
        )

    worker_module.RUNNER_REGISTRY["batched_collision_metric"] = (
        worker_module.RunnerMetricEntry(
            metric_name="batched_collision_metric",
            queue="heavy",
            entrypoint="batched_collision_plugin:run",
            output=_table_output(),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-batched-collision",
            "metric": "batched_collision_metric",
            "input": {
                "location": _feature_collection(),
                "sector_filters": [
                    {"key": "retail", "value": "^46.*"},
                    {"key": "retail", "value": "^47.*"},
                ],
            },
        },
        task_id="task-id",
    )

    assert result["status"] == "failed"
    assert result["error"] == {
        "type": "invalid_result",
        "message": "Expanded table output columns must be unique.",
    }
    assert (
        _decode_stored_result(
            worker_module,
            fake_redis,
            "job-batched-collision",
        )
        == result
    )
