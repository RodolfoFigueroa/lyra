import pytest
from lyra.sdk.models.plugin import PluginManifest
from pydantic import ValidationError


def _manifest(metric_overrides: dict | None = None) -> dict:
    metric = {
        "name": "light_metric",
        "description": "A lightweight metric.",
        "parameters": [
            {"name": "value", "type": "int", "required": True, "default": None}
        ],
        "returns_file": False,
        "tavi_hint": "",
        "request_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
        },
        "execution": {
            "profile": "lightweight",
            "queue": "lightweight",
            "timeout_seconds": 30,
        },
        "callable": {"mode": "single", "calculate": "fake_plugin:calculate"},
    }
    if metric_overrides:
        metric.update(metric_overrides)
    return {
        "schema_version": 1,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [metric],
    }


def test_manifest_accepts_single_metric_contract() -> None:
    manifest = PluginManifest.model_validate(_manifest())

    assert manifest.plugin.name == "fake-plugin"
    assert manifest.metrics[0].callable.calculate == "fake_plugin:calculate"


def test_manifest_rejects_single_metric_without_calculate() -> None:
    raw = _manifest({"callable": {"mode": "single"}})

    with pytest.raises(ValidationError, match="single metrics must define"):
        PluginManifest.model_validate(raw)


def test_manifest_rejects_batched_metric_without_required_callables() -> None:
    raw = _manifest({"callable": {"mode": "batched", "prepare": "fake:prepare"}})

    with pytest.raises(ValidationError, match="batched metrics are missing"):
        PluginManifest.model_validate(raw)


def test_manifest_rejects_duplicate_metric_names() -> None:
    raw = _manifest()
    raw["metrics"].append(raw["metrics"][0].copy())

    with pytest.raises(ValidationError, match="duplicate metric name"):
        PluginManifest.model_validate(raw)
