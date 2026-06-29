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

KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


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


def _batched_item_properties(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties = {
        "key": {
            "type": "string",
            "pattern": KEY_PATTERN,
            "minLength": 1,
            "maxLength": 64,
        },
        "value": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        },
        "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
        },
    }
    if overrides:
        properties.update(overrides)
    return properties


def _batched_item_schema(
    *,
    required: list[str] | None = None,
    properties: dict[str, Any] | None = None,
    additional_properties: bool = False,
    item_type: str = "object",
) -> dict[str, Any]:
    return {
        "type": item_type,
        "required": ["key", "value"] if required is None else required,
        "properties": _batched_item_properties() if properties is None else properties,
        "additionalProperties": additional_properties,
    }


def _batched_source_schema(
    *,
    items: dict[str, Any] | None = None,
    include_min_items: bool = True,
    include_max_items: bool = True,
    include_unique_items: bool = True,
) -> dict[str, Any]:
    schema = {
        "type": "array",
        "items": _batched_item_schema() if items is None else items,
    }
    if include_min_items:
        schema["minItems"] = 1
    if include_max_items:
        schema["maxItems"] = 20
    if include_unique_items:
        schema["uniqueItems"] = True
    return schema


def _batched_request_schema(
    *,
    source_schema: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required or ["location", "sector_filters"],
        "properties": {
            "location": {},
            "sector_filters": source_schema or _batched_source_schema(),
        },
        "additionalProperties": False,
    }


def _batched_column(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    column = {
        "source": "sector_filters",
        "name_template": "job_accessibility_{key}",
        "type": "number",
        "unit": "jobs",
        "description_template": "Job accessibility for {label}.",
        "batching_reason": (
            "Reuses the network graph and travel-time matrix across filters."
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
    name_template: str = "job_accessibility_{key}",
    description_template: str = "Job accessibility for {label}.",
) -> TableMetricOutputV2:
    return TableMetricOutputV2.model_validate(
        {
            "kind": "table",
            "columns": [] if columns is None else columns,
            "batched_columns": [
                {
                    "source": "sector_filters",
                    "name_template": name_template,
                    "type": "number",
                    "unit": "jobs",
                    "description_template": description_template,
                    "batching_reason": (
                        "Reuses the network graph and travel-time matrix across "
                        "filters."
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
    assert output.batched_columns[0].source == "sector_filters"


def test_manifest_v2_accepts_batched_table_output_without_label_schema() -> None:
    properties = _batched_item_properties()
    properties.pop("label")
    manifest = PluginManifestV2.model_validate(
        _manifest(
            {
                "request_schema": _batched_request_schema(
                    source_schema=_batched_source_schema(
                        items=_batched_item_schema(properties=properties),
                    ),
                ),
                "output": _batched_output(),
            }
        )
    )

    output = manifest.metrics[0].output
    assert isinstance(output, TableMetricOutputV2)
    assert output.batched_columns[0].source == "sector_filters"


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
                            "description": "Total jobs across all filters.",
                        }
                    ],
                ),
            }
        )
    )

    output = manifest.metrics[0].output
    assert output.kind == "table"
    assert output.columns[0].name == "total_jobs"
    assert output.batched_columns[0].name_template == "job_accessibility_{key}"


def test_manifest_v2_rejects_table_output_without_columns() -> None:
    raw = _manifest({"output": {"kind": "table", "columns": []}})

    with pytest.raises(ValidationError, match="columns or batched_columns"):
        PluginManifestV2.model_validate(raw)


@pytest.mark.parametrize(
    ("column_overrides", "match"),
    [
        ({"name_template": "job_accessibility"}, r"\{key\}"),
        ({"name_template": "job_accessibility_{value}"}, r"\{value\}"),
        ({"name_template": "job_accessibility_{label}_{key}"}, "unsupported"),
        ({"description_template": "Job accessibility for {value}."}, r"\{value\}"),
        ({"description_template": "Job accessibility for {other}."}, "unsupported"),
    ],
)
def test_manifest_v2_rejects_invalid_batched_templates(
    column_overrides: dict[str, Any],
    match: str,
) -> None:
    raw = _manifest(
        {
            "request_schema": _batched_request_schema(),
            "output": _batched_output(
                batched_columns=[_batched_column(column_overrides)],
            ),
        }
    )

    with pytest.raises(ValidationError, match=match):
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
        ({"type": "string"}, ["location", "sector_filters"], "array"),
        (
            _batched_source_schema(include_min_items=False),
            ["location", "sector_filters"],
            "minItems",
        ),
        (
            _batched_source_schema(include_max_items=False),
            ["location", "sector_filters"],
            "maxItems",
        ),
        (
            _batched_source_schema(include_unique_items=False),
            ["location", "sector_filters"],
            "uniqueItems",
        ),
        (
            _batched_source_schema(items=_batched_item_schema(item_type="string")),
            ["location", "sector_filters"],
            "objects",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(additional_properties=True),
            ),
            ["location", "sector_filters"],
            "additionalProperties",
        ),
        (
            _batched_source_schema(items=_batched_item_schema(required=["key"])),
            ["location", "sector_filters"],
            "key and value",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(required=["key", "value", "label"])
            ),
            ["location", "sector_filters"],
            "key and value only",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(
                    properties={
                        **_batched_item_properties(),
                        "pattern": {"type": "string"},
                    }
                )
            ),
            ["location", "sector_filters"],
            "unsupported properties",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(
                    properties={
                        "key": _batched_item_properties()["key"],
                        "label": _batched_item_properties()["label"],
                    }
                )
            ),
            ["location", "sector_filters"],
            "key and value properties",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(
                    properties=_batched_item_properties(
                        {"key": {"type": "integer"}},
                    )
                )
            ),
            ["location", "sector_filters"],
            "key",
        ),
        (
            _batched_source_schema(
                items=_batched_item_schema(
                    properties=_batched_item_properties(
                        {"label": {"type": "integer"}},
                    )
                )
            ),
            ["location", "sector_filters"],
            "label",
        ),
        (
            _batched_source_schema(),
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
    request_schema["properties"].pop("sector_filters")
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
    assert info_payload["output"]["batched_columns"][0]["source"] == "sector_filters"
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]


def test_worker_expands_batched_column_descriptions_from_label_or_key(
    worker_module: Any,
) -> None:
    output = _table_output(
        description_template="Job accessibility for {label} ({key}).",
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
        worker_module.RunnerMetricEntryV2(
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
        worker_module.RunnerMetricEntryV2(
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
