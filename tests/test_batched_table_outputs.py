import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.models import JobEnvelope, PluginManifestV2, TableJobResult
from lyra.sdk.models.plugin_v2 import TableMetricOutputV2
from pydantic import ValidationError

from lyra_app import registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo


def _metric(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    metric = {
        "name": "light_metric",
        "description": "A lightweight metric.",
        "request_schema": {
            "type": "object",
            "required": ["location", "value"],
            "properties": {"location": {}, "value": {"type": "integer"}},
            "additionalProperties": False,
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
        "spatial_inputs": {"location": "location"},
        "execution": {"queue": "lightweight"},
        "entrypoint": "fake_plugin.runner:run",
    }
    if overrides:
        metric.update(overrides)
    return metric


def _manifest(metric_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [_metric(metric_overrides)],
    }


def _batched_request_schema(
    *,
    source_schema: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required or ["location", "sectors"],
        "properties": {
            "location": {},
            "sectors": source_schema
            or {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
            },
        },
        "additionalProperties": False,
    }


def _batched_column(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    column = {
        "source": "sectors",
        "name_template": "job_accessibility_{value}",
        "type": "number",
        "unit": "jobs",
        "description_template": "Job accessibility for sector {value}.",
        "batching_reason": (
            "Reuses the network graph and travel-time matrix across sectors."
        ),
    }
    if overrides:
        column.update(overrides)
    return column


def _batched_output(
    *,
    columns: list[dict[str, Any]] | None = None,
    batched_columns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "table",
        "columns": [] if columns is None else columns,
        "batched_columns": (
            [_batched_column()] if batched_columns is None else batched_columns
        ),
    }


def _table_output(
    *,
    columns: list[dict[str, Any]] | None = None,
    name_template: str = "job_accessibility_{value}",
) -> TableMetricOutputV2:
    return TableMetricOutputV2.model_validate(
        {
            "kind": "table",
            "columns": [] if columns is None else columns,
            "batched_columns": [
                {
                    "source": "sectors",
                    "name_template": name_template,
                    "type": "number",
                    "unit": "jobs",
                    "description_template": "Job accessibility for sector {value}.",
                    "batching_reason": (
                        "Reuses the network graph and travel-time matrix across "
                        "sectors."
                    ),
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
def reset_catalog() -> None:
    registry.reset_catalog()


@pytest.fixture
def worker_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("LYRA_PLUGIN_REPOS", raising=False)
    worker = importlib.import_module("lyra_app.worker")
    worker.RUNNER_REGISTRY.clear()
    yield worker
    worker.RUNNER_REGISTRY.clear()


def _decode_stored_result(
    worker: Any,
    redis: FakeRedisSync,
    job_id: str,
) -> dict[str, Any]:
    return json.loads(redis.values[worker.job_store.result_key(job_id)])


def test_manifest_v2_accepts_batched_table_output() -> None:
    manifest = PluginManifestV2.model_validate(
        _manifest(
            {
                "request_schema": _batched_request_schema(),
                "output": _batched_output(),
            }
        )
    )

    output = manifest.metrics[0].output
    assert output.kind == "table"
    assert output.columns == []
    assert output.batched_columns[0].source == "sectors"


def test_manifest_v2_accepts_mixed_static_and_batched_table_output() -> None:
    manifest = PluginManifestV2.model_validate(
        _manifest(
            {
                "request_schema": _batched_request_schema(),
                "output": _batched_output(
                    columns=[
                        {
                            "name": "total_jobs",
                            "type": "integer",
                            "unit": "jobs",
                            "description": "Total jobs across all sectors.",
                        }
                    ],
                ),
            }
        )
    )

    output = manifest.metrics[0].output
    assert output.kind == "table"
    assert output.columns[0].name == "total_jobs"
    assert output.batched_columns[0].name_template == "job_accessibility_{value}"


def test_manifest_v2_rejects_table_output_without_columns() -> None:
    raw = _manifest({"output": {"kind": "table", "columns": []}})

    with pytest.raises(ValidationError, match="columns or batched_columns"):
        PluginManifestV2.model_validate(raw)


@pytest.mark.parametrize(
    "column_overrides",
    [
        {"name_template": "job_accessibility"},
        {"description_template": "Job accessibility."},
    ],
)
def test_manifest_v2_rejects_batched_templates_without_value(
    column_overrides: dict[str, Any],
) -> None:
    raw = _manifest(
        {
            "request_schema": _batched_request_schema(),
            "output": _batched_output(
                batched_columns=[_batched_column(column_overrides)],
            ),
        }
    )

    with pytest.raises(ValidationError, match=r"\{value\}"):
        PluginManifestV2.model_validate(raw)


@pytest.mark.parametrize("batching_reason", [None, ""])
def test_manifest_v2_rejects_missing_or_empty_batching_reason(
    batching_reason: str | None,
) -> None:
    column = _batched_column()
    if batching_reason is None:
        column.pop("batching_reason")
    else:
        column["batching_reason"] = batching_reason
    raw = _manifest(
        {
            "request_schema": _batched_request_schema(),
            "output": _batched_output(batched_columns=[column]),
        }
    )

    with pytest.raises(ValidationError, match="batching_reason"):
        PluginManifestV2.model_validate(raw)


@pytest.mark.parametrize(
    ("source_schema", "required", "match"),
    [
        ({"type": "string"}, ["location", "sectors"], "array"),
        (
            {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
                "uniqueItems": True,
            },
            ["location", "sectors"],
            "minItems",
        ),
        (
            {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "uniqueItems": True,
            },
            ["location", "sectors"],
            "maxItems",
        ),
        (
            {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
            },
            ["location", "sectors"],
            "uniqueItems",
        ),
        (
            {
                "type": "array",
                "items": {"type": "object"},
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
            },
            ["location", "sectors"],
            "items",
        ),
        (
            {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
            },
            ["location"],
            "required",
        ),
    ],
)
def test_manifest_v2_rejects_invalid_batched_source_schema(
    source_schema: dict[str, Any],
    required: list[str],
    match: str,
) -> None:
    raw = _manifest(
        {
            "request_schema": _batched_request_schema(
                source_schema=source_schema,
                required=required,
            ),
            "output": _batched_output(),
        }
    )

    with pytest.raises(ValidationError, match=match):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_missing_batched_source_property() -> None:
    request_schema = _batched_request_schema()
    request_schema["properties"].pop("sectors")
    raw = _manifest(
        {
            "request_schema": request_schema,
            "output": _batched_output(),
        }
    )

    with pytest.raises(ValidationError, match="properties"):
        PluginManifestV2.model_validate(raw)


def test_catalog_refresh_preserves_batched_column_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(
        repo,
        _manifest(
            {
                "request_schema": _batched_request_schema(),
                "output": _batched_output(),
            }
        ),
    )
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    registry.refresh_catalog()
    info = registry.get_metric_info("light_metric")

    assert info is not None
    info_payload = info.model_dump()
    assert info_payload["output"]["columns"] == []
    assert info_payload["output"]["batched_columns"][0]["source"] == "sectors"
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]


def test_batched_table_result_expands_columns_from_input_order(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=[f"job_accessibility_{sector}" for sector in job.input["sectors"]],
            data=[[1.5, 2.5]],
        )

    worker_module.RUNNER_REGISTRY["batched_metric"] = worker_module.RunnerMetricEntryV2(
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
            "input": {"location": _feature_collection(), "sectors": ["32", "01"]},
        },
        task_id="task-id",
    )

    assert result == {
        "kind": "table",
        "job_id": "job-batched",
        "status": "succeeded",
        "index": ["area-1"],
        "columns": ["job_accessibility_32", "job_accessibility_01"],
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
            columns=["total_jobs", "job_accessibility_01"],
            data=[[100, 3.5]],
        )

    worker_module.RUNNER_REGISTRY["mixed_batched_metric"] = (
        worker_module.RunnerMetricEntryV2(
            metric_name="mixed_batched_metric",
            queue="heavy",
            entrypoint="mixed_batched_plugin:run",
            output=_table_output(
                columns=[
                    {
                        "name": "total_jobs",
                        "type": "integer",
                        "unit": "jobs",
                        "description": "Total jobs across all sectors.",
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
            "input": {"location": _feature_collection(), "sectors": ["01"]},
        },
        task_id="task-id",
    )

    assert result["status"] == "succeeded"
    assert result["columns"] == ["total_jobs", "job_accessibility_01"]
    assert result["data"] == [[100, 3.5]]


@pytest.mark.parametrize(
    ("columns", "data"),
    [
        (["job_accessibility_01"], [[1.0]]),
        (["job_accessibility_01", "job_accessibility_32", "extra"], [[1.0, 2.0, 3.0]]),
        (["job_accessibility_32", "job_accessibility_01"], [[2.0, 1.0]]),
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
        worker_module.RunnerMetricEntryV2(
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
            "input": {"location": _feature_collection(), "sectors": ["01", "32"]},
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
            columns=["job_accessibility_01"],
            data=[[value]],
        )

    worker_module.RUNNER_REGISTRY["invalid_batched_value_metric"] = (
        worker_module.RunnerMetricEntryV2(
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
            "input": {"location": _feature_collection(), "sectors": ["01"]},
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


def test_batched_table_generated_column_collision_persists_failed_result(
    monkeypatch: pytest.MonkeyPatch,
    worker_module: Any,
) -> None:
    def run(job: JobEnvelope, context: Any) -> TableJobResult:  # noqa: ARG001
        return TableJobResult(
            job_id=job.job_id,
            index=["area-1"],
            columns=["value_1"],
            data=[[1]],
        )

    worker_module.RUNNER_REGISTRY["batched_collision_metric"] = (
        worker_module.RunnerMetricEntryV2(
            metric_name="batched_collision_metric",
            queue="heavy",
            entrypoint="batched_collision_plugin:run",
            output=_table_output(name_template="value_{value}"),
            run=run,
        )
    )
    fake_redis = FakeRedisSync()
    monkeypatch.setattr(worker_module.job_store, "redis_client_sync", fake_redis)

    result = worker_module.execute_job(
        {
            "job_id": "job-batched-collision",
            "metric": "batched_collision_metric",
            "input": {"location": _feature_collection(), "sectors": [1, "1"]},
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
