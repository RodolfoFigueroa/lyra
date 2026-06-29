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
    inputs: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    queue: str = "lightweight",
    entrypoint: str = "fake_plugin.runner:run",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputs": inputs
        or {
            "location": {"kind": "location"},
            "value": {"kind": "integer"},
        },
        "output": output
        or {
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
        "queue": queue,
        "entrypoint": entrypoint,
    }


def _manifest(
    *,
    plugin_name: str = "fake-plugin",
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
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


def test_catalog_refresh_reads_v3_manifests_without_importing_plugin_code(
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
    assert info_payload["output"]["kind"] == "table"
    assert info_payload["output"]["columns"][0]["name"] == "value"
    assert info_payload["request_schema"]["required"] == ["location", "value"]
    assert info_payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]
    assert "GeoJSONLocationWrapperV3" in info_payload["request_schema"]["$defs"]
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


def test_catalog_refresh_reads_v3_file_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        name="raster_metric",
        description="A raster metric.",
        inputs={
            "bounds": {"kind": "bounds"},
            "year": {"kind": "integer", "minimum": 2020, "maximum": 2026},
        },
        output={
            "kind": "file",
            "media_type": "image/tiff",
            "extensions": [".tif", ".tiff"],
        },
        queue="heavy",
        entrypoint="fake_plugin.runner:run_raster",
    )
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    registry.refresh_catalog()
    info = registry.get_metric_info("raster_metric")
    entry = registry.get_metric_entry("raster_metric")

    assert info is not None
    assert info.output.model_dump(mode="json") == {
        "kind": "file",
        "media_type": "image/tiff",
        "extensions": [".tif", ".tiff"],
    }
    assert info.request_schema["required"] == ["bounds", "year"]
    assert "GeoJSONBoundsWrapperV3" in info.request_schema["$defs"]
    assert entry is not None
    assert entry.queue == "heavy"


def test_catalog_refresh_rejects_invalid_request_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        inputs={
            "location": {"kind": "location"},
            "bad": {
                "kind": "json_schema",
                "schema": {"type": "not-a-json-schema-type"},
            },
        }
    )
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


def test_validate_metric_payload_uses_compiled_json_schema_escape_hatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        inputs={
            "location": {"kind": "location"},
            "value": {
                "kind": "json_schema",
                "schema": {
                    "type": "number",
                    "minimum": 0,
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
                "value": -1,
            },
        )

    assert exc_info.value.errors[0]["type"] == "minimum"


def test_catalog_refresh_rejects_legacy_v2_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    legacy_metric = {
        "name": "legacy_metric",
        "description": "Legacy metric.",
        "request_schema": {
            "type": "object",
            "required": ["location"],
            "properties": {"location": {}},
        },
        "spatial_inputs": {"location": "location"},
        "output": {
            "kind": "table",
            "columns": [
                {
                    "name": "value",
                    "type": "integer",
                    "unit": "count",
                    "description": "Value.",
                }
            ],
        },
        "execution": {"queue": "legacy"},
        "entrypoint": "legacy_plugin.runner:run",
    }
    _write_manifest(
        repo,
        {
            "schema_version": 2,
            "plugin": {"name": "legacy-plugin", "version": "1.0.0"},
            "metrics": [legacy_metric],
        },
    )
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    with pytest.raises(RuntimeError, match=r"Plugin manifest .* is invalid"):
        registry.refresh_catalog()


def test_catalog_builds_spatial_schema_for_location_and_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        inputs={
            "location": {"kind": "location"},
            "bounds": {"kind": "bounds"},
            "value": {"kind": "integer"},
        },
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
    assert "GeoJSONLocationWrapperV3" in schema_defs
    assert "GeoJSONBoundsWrapperV3" in schema_defs
