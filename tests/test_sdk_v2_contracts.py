from datetime import UTC, datetime
from typing import Any

import pytest
from lyra.sdk.context import RunContext
from lyra.sdk.models import (
    DataTypesResponse,
    FailedJobResult,
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobEvent,
    JobLinks,
    JobStatusInfo,
    PluginManifestV2,
    TableJobResult,
    parse_job_result,
)
from pydantic import ValidationError


def _metric(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    metric = {
        "name": "light_metric",
        "description": "A lightweight metric.",
        "request_schema": {
            "type": "object",
            "required": ["location", "value"],
            "properties": {"value": {"type": "integer"}},
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
    metric["request_schema"]["properties"]["location"] = {}
    if overrides:
        metric.update(overrides)
    return metric


def _manifest(
    metric_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [_metric(metric_overrides)],
    }


def test_job_models_accept_v2_contract_payloads() -> None:
    envelope = JobEnvelope.model_validate(
        {
            "job_id": "job-1",
            "metric": "light_metric",
            "input": {"value": 1},
            "idempotency_key": "idem-1",
            "metadata": {"source": "test"},
        },
    )
    event = JobEvent.model_validate(
        {
            "job_id": "job-1",
            "event": "progress",
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "data": {"percent": 50},
        },
    )
    result = parse_job_result(
        {
            "kind": "table",
            "job_id": "job-1",
            "status": "succeeded",
            "index": ["area-1"],
            "columns": ["value"],
            "data": [[2]],
        },
    )

    assert envelope.input == {"value": 1}
    assert event.timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert isinstance(result, TableJobResult)
    assert result.status == "succeeded"


@pytest.mark.parametrize(
    "payload",
    [
        {"job_id": "", "metric": "light_metric", "input": {}},
        {"job_id": "job-1", "metric": "", "input": {}},
        {"job_id": "job-1", "metric": "light_metric"},
        {"job_id": "job-1", "metric": "light_metric", "input": {}, "extra": True},
    ],
)
def test_job_envelope_rejects_invalid_payloads(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate(payload)


def test_job_event_rejects_empty_event_name() -> None:
    with pytest.raises(ValidationError):
        JobEvent.model_validate(
            {
                "job_id": "job-1",
                "event": "",
                "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
                "data": {},
            },
        )


def test_terminal_result_rejects_non_terminal_status() -> None:
    with pytest.raises(ValidationError):
        TableJobResult.model_validate(
            {
                "kind": "table",
                "job_id": "job-1",
                "status": "progress",
                "index": ["area-1"],
                "columns": ["value"],
                "data": [[1]],
            },
        )


def test_failed_terminal_result_accepts_error_payload() -> None:
    result = FailedJobResult(
        job_id="job-1",
        error={"type": "worker", "message": "boom"},
    )

    assert result.kind == "failed"
    assert result.status == "failed"


def test_job_api_models_accept_public_payloads() -> None:
    request = JobCreateRequest.model_validate(
        {"metric": "light_metric", "input": {"value": 1}, "idempotency_key": "idem"}
    )
    response = JobCreateResponse(
        job_id="job-1",
        metric="light_metric",
        status="queued",
        links=JobLinks(
            self="/jobs/job-1",
            events="/jobs/job-1/events",
            result="/jobs/job-1/result",
        ),
    )
    status = JobStatusInfo.model_validate(
        {
            "job_id": "job-1",
            "metric": "light_metric",
            "status": "started",
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        }
    )

    assert request.metric == "light_metric"
    assert response.links.events == "/jobs/job-1/events"
    assert status.updated_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_data_types_response_accepts_grouped_schema_payload() -> None:
    response = DataTypesResponse.model_validate(
        {
            "location": [
                {
                    "data_type": "geojson",
                    "description": "GeoJSON locations.",
                    "wrapper_schema": {
                        "type": "object",
                        "required": ["data_type", "value"],
                    },
                }
            ],
            "bounds": [
                {
                    "data_type": "geojson",
                    "description": "One GeoJSON bounds geometry.",
                    "wrapper_schema": {
                        "type": "object",
                        "required": ["data_type", "value"],
                    },
                }
            ],
        }
    )

    assert response.location[0].data_type == "geojson"
    assert response.bounds[0].wrapper_schema["type"] == "object"


def test_manifest_v2_accepts_schema_backed_metric_contract() -> None:
    manifest = PluginManifestV2.model_validate(_manifest())

    assert manifest.schema_version == 2
    assert manifest.plugin.name == "fake-plugin"
    assert manifest.metrics[0].spatial_inputs == {"location": "location"}
    assert manifest.metrics[0].execution.queue == "lightweight"
    assert manifest.metrics[0].entrypoint == "fake_plugin.runner:run"


def test_manifest_v2_requires_spatial_inputs() -> None:
    raw = _manifest()
    raw["metrics"][0].pop("spatial_inputs")

    with pytest.raises(ValidationError, match="spatial_inputs"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_empty_spatial_inputs() -> None:
    raw = _manifest({"spatial_inputs": {}})

    with pytest.raises(ValidationError):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_invalid_spatial_input_kind() -> None:
    raw = _manifest({"spatial_inputs": {"location": "area"}})

    with pytest.raises(ValidationError):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_empty_spatial_input_field_name() -> None:
    raw = _manifest({"spatial_inputs": {"": "location"}})

    with pytest.raises(ValidationError, match="non-empty"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_undeclared_spatial_input_field() -> None:
    raw = _manifest({"spatial_inputs": {"missing": "location"}})

    with pytest.raises(ValidationError, match="properties"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_optional_spatial_input_field() -> None:
    raw = _manifest()
    raw["metrics"][0]["request_schema"]["required"] = ["value"]

    with pytest.raises(ValidationError, match="required"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_extra_metric_fields() -> None:
    raw = _manifest(
        {
            "unexpected": True,
        },
    )

    with pytest.raises(ValidationError, match="Extra inputs"):
        PluginManifestV2.model_validate(raw)


@pytest.mark.parametrize(
    "entrypoint",
    [
        "fake_plugin.runner.run",
        "fake_plugin.runner:run:again",
        "fake-plugin.runner:run",
        "fake_plugin.runner:",
        ":run",
    ],
)
def test_manifest_v2_rejects_invalid_entrypoint_strings(entrypoint: str) -> None:
    raw = _manifest({"entrypoint": entrypoint})

    with pytest.raises(ValidationError, match="module:function"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_duplicate_metric_names() -> None:
    raw = _manifest()
    raw["metrics"].append(_metric())

    with pytest.raises(ValidationError, match="duplicate metric name"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_invalid_schema_version() -> None:
    raw = _manifest()
    raw["schema_version"] = 1

    with pytest.raises(ValidationError):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_invalid_request_schema() -> None:
    raw = _manifest({"request_schema": {"type": "not-a-json-schema-type"}})

    with pytest.raises(ValidationError, match="invalid request_schema"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_missing_output() -> None:
    raw = _manifest()
    raw["metrics"][0].pop("output")

    with pytest.raises(ValidationError, match="output"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_duplicate_table_output_columns() -> None:
    raw = _manifest(
        {
            "output": {
                "kind": "table",
                "columns": [
                    {
                        "name": "value",
                        "type": "integer",
                        "unit": "count",
                        "description": "One.",
                    },
                    {
                        "name": "value",
                        "type": "integer",
                        "unit": "count",
                        "description": "Two.",
                    },
                ],
            }
        }
    )

    with pytest.raises(ValidationError, match="duplicate"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_rejects_table_output_without_location_field() -> None:
    raw = _manifest({"spatial_inputs": {"bounds": "bounds"}})
    raw["metrics"][0]["request_schema"]["properties"] = {"bounds": {}, "value": {}}
    raw["metrics"][0]["request_schema"]["required"] = ["bounds", "value"]

    with pytest.raises(ValidationError, match="location"):
        PluginManifestV2.model_validate(raw)


def test_manifest_v2_accepts_file_output() -> None:
    raw = _manifest(
        {
            "output": {
                "kind": "file",
                "media_type": "image/tiff",
                "extensions": [".tif", ".tiff"],
            }
        }
    )

    manifest = PluginManifestV2.model_validate(raw)

    assert manifest.metrics[0].output.kind == "file"


def test_manifest_v2_rejects_invalid_file_output_extension() -> None:
    raw = _manifest(
        {
            "output": {
                "kind": "file",
                "media_type": "image/tiff",
                "extensions": ["tif"],
            }
        }
    )

    with pytest.raises(ValidationError, match="extension"):
        PluginManifestV2.model_validate(raw)


def test_run_context_is_public_protocol() -> None:
    assert RunContext.__name__ == "RunContext"
