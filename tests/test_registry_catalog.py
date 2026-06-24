import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from lyra_app import registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo


def _metric(
    *,
    name: str = "light_metric",
    description: str = "A metric.",
    request_schema: dict[str, Any] | None = None,
    result_schema: dict[str, Any] | None = None,
    spatial_inputs: dict[str, str] | None = None,
    queue: str = "lightweight",
    entrypoint: str = "fake_plugin.runner:run",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "request_schema": request_schema
        or {
            "type": "object",
            "required": ["location", "value"],
            "properties": {"location": {}, "value": {"type": "integer"}},
            "additionalProperties": False,
        },
        "spatial_inputs": spatial_inputs or {"location": "location"},
        "result_schema": result_schema,
        "execution": {"queue": queue},
        "entrypoint": entrypoint,
    }


def _manifest(
    *,
    plugin_name: str = "fake-plugin",
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plugin": {"name": plugin_name, "version": "1.0.0"},
        "metrics": [metric or _metric()],
    }


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _synced_repo(repo: Path, *, changed: bool = False) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=changed)


@pytest.fixture(autouse=True)
def reset_catalog() -> None:
    registry.reset_catalog()


def test_catalog_refresh_reads_v2_manifests_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    def fail_import(name: str, package: str | None = None) -> object:  # noqa: ARG001
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    result = registry.refresh_catalog()
    info = registry.get_metric_info("light_metric")
    entry = registry.get_metric_entry("light_metric")

    assert result.catalog_changed is True
    assert info is not None
    info_payload = info.model_dump()
    assert info_payload["name"] == "light_metric"
    assert info_payload["description"] == "A metric."
    assert info_payload["result_schema"] is None
    assert info_payload["request_schema"]["required"] == ["location", "value"]
    assert info_payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]
    assert "GeoJSONWrapper" in info_payload["request_schema"]["$defs"]
    assert entry is not None
    assert entry.queue == "lightweight"
    assert entry.entrypoint == "fake_plugin.runner:run"


def test_catalog_refresh_rejects_duplicate_metric_names_across_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_repo = tmp_path / "repo-1"
    second_repo = tmp_path / "repo-2"
    _write_manifest(first_repo, _manifest(plugin_name="plugin-a"))
    _write_manifest(second_repo, _manifest(plugin_name="plugin-b"))
    monkeypatch.setattr(
        registry,
        "sync_catalog_repos",
        lambda: [_synced_repo(first_repo), _synced_repo(second_repo)],
    )

    with pytest.raises(RuntimeError, match="Duplicate metric name"):
        registry.refresh_catalog()


@pytest.mark.parametrize("schema_field", ["request_schema", "result_schema"])
def test_catalog_refresh_rejects_invalid_json_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_field: str,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric()
    metric[schema_field] = {"type": "not-a-json-schema-type"}
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    with pytest.raises(RuntimeError, match=r"Plugin manifest .* is invalid"):
        registry.refresh_catalog()


def test_catalog_fingerprint_changes_only_when_manifest_content_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    first = registry.refresh_catalog()
    second = registry.refresh_catalog()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    third = registry.refresh_catalog()

    assert second.catalog_changed is False
    assert second.catalog_fingerprint == first.catalog_fingerprint
    assert third.catalog_changed is True
    assert third.catalog_fingerprint != first.catalog_fingerprint


def test_validate_metric_payload_uses_manifest_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])
    registry.refresh_catalog()

    payload = {
        "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
        "value": 1,
    }
    assert registry.validate_metric_payload("light_metric", payload) == payload

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload(
            "light_metric",
            {
                "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
                "value": "wrong",
            },
        )

    assert exc_info.value.errors[0]["type"] == "type"


def test_validate_metric_payload_honors_declared_json_schema_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        request_schema={
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "required": ["location", "value"],
            "properties": {
                "location": {},
                "value": {
                    "type": "number",
                    "minimum": 0,
                    "exclusiveMinimum": True,
                },
            },
        }
    )
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])
    registry.refresh_catalog()

    valid_payload = {
        "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
        "value": 1,
    }
    assert (
        registry.validate_metric_payload("light_metric", valid_payload) == valid_payload
    )

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload(
            "light_metric",
            {
                "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
                "value": 0,
            },
        )

    assert exc_info.value.errors[0]["type"] == "minimum"


def test_catalog_refresh_rejects_raw_geojson_request_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        request_schema={
            "type": "object",
            "required": ["location"],
            "properties": {"location": {"$ref": "#/$defs/geoJSON"}},
            "$defs": {
                "geoJSON": {
                    "type": "object",
                    "required": ["type", "features", "crs"],
                    "properties": {
                        "type": {"const": "FeatureCollection"},
                        "features": {"type": "array"},
                        "crs": {"type": "object"},
                    },
                },
            },
        },
    )
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    with pytest.raises(RuntimeError, match="raw GeoJSON"):
        registry.refresh_catalog()


def test_catalog_builds_spatial_schema_for_location_and_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        request_schema={
            "type": "object",
            "required": ["location", "bounds", "value"],
            "properties": {
                "location": {},
                "bounds": {},
                "value": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        spatial_inputs={"location": "location", "bounds": "bounds"},
    )
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])
    registry.refresh_catalog()

    payload = {
        "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
        "bounds": {"data_type": "cvegeo_list", "value": ["090020001"]},
        "value": 1,
    }

    assert registry.validate_metric_payload("light_metric", payload) == payload

    info = registry.get_metric_info("light_metric")
    assert info is not None
    schema_defs = info.request_schema["$defs"]
    assert "GeoJSONWrapper" in schema_defs
    assert "SingleGeoJSONWrapper" in schema_defs
