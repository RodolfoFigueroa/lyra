from datetime import UTC, datetime
from typing import Any

import pytest
from lyra.sdk.context import RunContext
from lyra.sdk.models import (
    JobCreateRequest,
    JobCreateResponse,
    JobEnvelope,
    JobEvent,
    JobLinks,
    JobResult,
    JobStatusInfo,
    PluginManifestV2,
)
from pydantic import ValidationError


def _metric(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    metric = {
        "name": "light_metric",
        "description": "A lightweight metric.",
        "request_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        },
        "result_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        },
        "execution": {"queue": "lightweight"},
        "entrypoint": "fake_plugin.runner:run",
    }
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
    result = JobResult.model_validate(
        {
            "job_id": "job-1",
            "status": "succeeded",
            "result": {"value": 2},
            "result_type": "json",
        },
    )

    assert envelope.input == {"value": 1}
    assert event.timestamp == datetime(2026, 1, 1, tzinfo=UTC)
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


def test_job_result_rejects_non_terminal_status() -> None:
    with pytest.raises(ValidationError):
        JobResult.model_validate(
            {"job_id": "job-1", "status": "progress", "result": {"value": 1}},
        )


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


def test_manifest_v2_accepts_schema_backed_metric_contract() -> None:
    manifest = PluginManifestV2.model_validate(_manifest())

    assert manifest.schema_version == 2
    assert manifest.plugin.name == "fake-plugin"
    assert manifest.metrics[0].execution.queue == "lightweight"
    assert manifest.metrics[0].entrypoint == "fake_plugin.runner:run"


def test_manifest_v2_rejects_legacy_metric_fields() -> None:
    raw = _manifest(
        {
            "parameters": [],
            "returns_file": False,
            "tavi_hint": "",
            "callable": {"mode": "single", "calculate": "fake:calculate"},
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


def test_manifest_v2_rejects_invalid_result_schema() -> None:
    raw = _manifest({"result_schema": {"type": "not-a-json-schema-type"}})

    with pytest.raises(ValidationError, match="invalid result_schema"):
        PluginManifestV2.model_validate(raw)


def test_run_context_is_public_protocol() -> None:
    assert RunContext.__name__ == "RunContext"
