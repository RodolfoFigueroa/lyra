import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.config import PluginRepoConfig
from lyra.sdk.types import JsonValue, validate_json_object

from lyra_app import registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.plugins import (
    MANIFEST_FILENAME,
    PluginLocation,
)
from tests.catalog_helpers import configure_catalog_sources, restart_catalog
from tests.config_helpers import load_test_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    directory_uri,
    smoke_plugin_uri,
)


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _metric(
    *,
    name: str = "light_metric",
    description: str = "A metric.",
    inputs: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
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
    }


def _manifest(
    *,
    plugin_name: str = "fake-plugin",
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "plugin": {"name": plugin_name, "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "metrics": [metric or _metric()],
    }


def _write_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture(autouse=True)
def reset_catalog(tmp_path: Path) -> Iterator[None]:
    registry.reset_catalog()
    load_test_config(
        tmp_path,
        metric_queues={
            "light_metric": "lightweight",
            "raster_metric": "heavy",
        },
    )
    yield
    registry.reset_catalog()
    clear_config_cache()


def test_catalog_refresh_reads_v4_manifests_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    def fail_import(name: str, package: str | None = None) -> object:  # ruff:ignore[unused-function-argument]
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    restart_catalog()
    info = registry.get_metric_info("light_metric")
    entry = registry.get_metric_entry("light_metric")

    assert info is not None
    info_payload = info.model_dump()
    assert info_payload["name"] == "light_metric"
    assert info_payload["description"] == "A metric."
    assert info_payload["spatial_inputs"] == {"location": "location"}
    assert info_payload["output"]["kind"] == "table"
    assert info_payload["output"]["columns"][0]["name"] == "value"
    assert info_payload["request_schema"]["required"] == ["location", "value"]
    assert info_payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]
    assert "GeoJSONLocation" in info_payload["request_schema"]["$defs"]
    assert entry is not None
    assert entry.queue == "lightweight"
    assert entry.repo_id == "repo"


def test_metric_search_text_is_derived_from_public_catalog_fields(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        inputs={
            "location": {"kind": "location"},
            "value": {
                "kind": "integer",
                "description": "Value supplied by the caller.",
            },
        },
        output={
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
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    search_text = registry.get_metric_search_text("light_metric")

    assert search_text is not None
    assert "light_metric" in search_text
    assert "A metric." in search_text
    assert "location" in search_text
    assert "value" in search_text
    assert "Value supplied by the caller." in search_text
    assert "table" in search_text
    assert "Example output value." in search_text
    assert "count" in search_text


def test_catalog_refresh_reads_directory_source_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "directory-plugin"
    _write_manifest(source, _manifest())
    config = get_config()
    config.plugins.repos = [
        PluginRepoConfig(id="directory-plugin", source=f"dir://{source}")
    ]

    def fail_import(name: str, package: str | None = None) -> object:  # ruff:ignore[unused-function-argument]
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    restart_catalog()
    entry = registry.get_metric_entry("light_metric")

    assert entry is not None
    assert entry.queue == "interactive"
    assert entry.repo_id == "directory-plugin"


def test_catalog_refresh_loads_smoke_directory_fixture(tmp_path: Path) -> None:
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[smoke_plugin_uri()],
    )

    restart_catalog()
    metric_names = sorted(info.name for info in registry.get_metrics_info())
    table_entry = registry.get_metric_entry("smoke_table_metric")
    file_entry = registry.get_metric_entry("smoke_file_metric")
    table_info = registry.get_metric_info("smoke_table_metric")
    file_info = registry.get_metric_info("smoke_file_metric")

    assert metric_names == [
        "smoke_cancel_metric",
        "smoke_file_metric",
        "smoke_table_metric",
    ]
    assert table_entry is not None
    assert table_entry.queue == "interactive"
    assert "GeoJSONLocation" in _json_object(table_entry.request_schema["$defs"])
    assert table_info is not None
    assert table_info.spatial_inputs == {"location": "location"}
    assert file_entry is not None
    assert file_entry.metric.output.kind == "file"
    assert file_info is not None
    assert file_info.spatial_inputs == {"location": "location"}


def test_catalog_refresh_rejects_missing_directory_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_source = tmp_path / "missing-smoke-plugin"
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[directory_uri(missing_source)],
    )

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_refresh_rejects_duplicate_metric_names_across_manifests(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_repo = tmp_path / "repo-1"
    second_repo = tmp_path / "repo-2"
    _write_manifest(first_repo, _manifest(plugin_name="plugin-a"))
    _write_manifest(second_repo, _manifest(plugin_name="plugin-b"))
    configure_catalog_sources(
        [
            PluginLocation(repo_id="first", path=first_repo),
            PluginLocation(repo_id="second", path=second_repo),
        ]
    )

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_refresh_reads_v4_file_metric(
    tmp_path: Path,
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
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    restart_catalog()
    info = registry.get_metric_info("raster_metric")
    entry = registry.get_metric_entry("raster_metric")

    assert info is not None
    assert info.output.model_dump(mode="json") == {
        "kind": "file",
        "media_type": "image/tiff",
        "extensions": [".tif", ".tiff"],
    }
    assert info.request_schema["required"] == ["bounds", "year"]
    assert "GeoJSONBounds" in _json_object(info.request_schema["$defs"])
    assert entry is not None
    assert entry.queue == "heavy"


def test_catalog_refresh_rejects_invalid_request_json_schema(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
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
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_fingerprint_changes_only_when_manifest_content_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    first = restart_catalog()
    second = restart_catalog()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    third = restart_catalog()

    assert second == first
    assert third != first


def test_public_catalog_fingerprint_changes_when_public_contract_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    restart_catalog()
    first = registry.get_public_catalog_fingerprint()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    restart_catalog()

    assert registry.get_public_catalog_fingerprint() != first


def test_factory_change_only_updates_internal_catalog_fingerprint(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    first = restart_catalog()
    public_fingerprint = registry.get_public_catalog_fingerprint()
    changed = _manifest()
    changed["factory"] = "fake_plugin.alternate:create_plugin"
    (repo / MANIFEST_FILENAME).write_text(json.dumps(changed), encoding="utf-8")
    second = restart_catalog()

    assert second != first
    assert registry.get_public_catalog_fingerprint() == public_fingerprint


def test_public_catalog_fingerprint_includes_spatial_inputs(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()
    info = registry.get_metric_info("light_metric")
    assert info is not None
    first = registry.public_catalog_fingerprint([info])
    changed = info.model_copy(
        update={"spatial_inputs": {"renamed_location": "location"}},
    )

    assert registry.public_catalog_fingerprint([changed]) != first


def test_public_catalog_fingerprint_ignores_queue_assignment_and_repo_id(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    config = get_config()

    restart_catalog()
    first = registry.get_public_catalog_fingerprint()
    config.plugins.repos[0].routing["light_metric"] = "batch"
    restart_catalog()
    after_queue_change = registry.get_public_catalog_fingerprint()

    assert after_queue_change == first

    config.plugins.repos[0].id = "custom-repo"
    configure_catalog_sources(
        [
            PluginLocation(repo_id="renamed", path=repo),
        ]
    )
    restart_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_public_catalog_fingerprint_is_deterministic_across_plugin_load_order(
    tmp_path: Path,
) -> None:
    load_test_config(
        tmp_path,
        repos=["owner/repo-a", "owner/repo-b"],
    )
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _write_manifest(
        repo_a,
        _manifest(
            plugin_name="plugin-a",
            metric=_metric(name="alpha_metric", description="Alpha metric."),
        ),
    )
    _write_manifest(
        repo_b,
        _manifest(
            plugin_name="plugin-b",
            metric=_metric(name="beta_metric", description="Beta metric."),
        ),
    )

    first_order = [
        PluginLocation(repo_id="repo-a", path=repo_a),
        PluginLocation(repo_id="repo-b", path=repo_b),
    ]
    second_order = list(reversed(first_order))

    configure_catalog_sources(first_order)
    restart_catalog()
    first = registry.get_public_catalog_fingerprint()

    configure_catalog_sources(second_order)
    restart_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_validate_metric_payload_uses_manifest_json_schema(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "value": 1,
        }
    )
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
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    valid_payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "value": 1,
        }
    )
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


def test_validate_metric_payload_rejects_duplicate_batch_keys(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        name="batched_metric",
        inputs={
            "location": {"kind": "location"},
            "sector_filters": {
                "kind": "batch",
                "max_items": 5,
                "value": {"kind": "string"},
            },
        },
        output={
            "kind": "table",
            "batched_columns": [
                {
                    "source": "sector_filters",
                    "name": "accessibility_{key}",
                    "type": "number",
                    "unit": "jobs",
                    "description": "Accessibility for {label}.",
                }
            ],
        },
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload(
            "batched_metric",
            {
                "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
                "sector_filters": [
                    {"key": "retail", "value": "^46.*"},
                    {"key": "retail", "value": "^47.*"},
                ],
            },
        )

    assert exc_info.value.errors == [
        {
            "loc": ["sector_filters"],
            "msg": "Batch input keys must be unique: retail.",
            "type": "unique_batch_keys",
        }
    ]


def test_validate_metric_payload_reports_duplicate_keys_per_batch_field(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        name="multi_batched_metric",
        inputs={
            "location": {"kind": "location"},
            "sector_filters": {
                "kind": "batch",
                "max_items": 5,
                "value": {"kind": "string"},
            },
            "destination_categories": {
                "kind": "batch",
                "max_items": 5,
                "value": {"kind": "string"},
            },
        },
        output={
            "kind": "table",
            "batched_columns": [
                {
                    "source": "sector_filters",
                    "name": "sector_{key}",
                    "type": "number",
                    "unit": "jobs",
                    "description": "Sector {label}.",
                },
                {
                    "source": "destination_categories",
                    "name": "destination_{key}",
                    "type": "number",
                    "unit": "destinations",
                    "description": "Destination {label}.",
                },
            ],
        },
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload(
            "multi_batched_metric",
            {
                "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
                "sector_filters": [
                    {"key": "retail", "value": "^46.*"},
                    {"key": "retail", "value": "^47.*"},
                ],
                "destination_categories": [
                    {"key": "schools", "value": "^61.*"},
                    {"key": "schools", "value": "^62.*"},
                ],
            },
        )

    assert exc_info.value.errors == [
        {
            "loc": ["sector_filters"],
            "msg": "Batch input keys must be unique: retail.",
            "type": "unique_batch_keys",
        },
        {
            "loc": ["destination_categories"],
            "msg": "Batch input keys must be unique: schools.",
            "type": "unique_batch_keys",
        },
    ]


def test_catalog_refresh_rejects_legacy_v2_manifest(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
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
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_builds_spatial_schema_for_location_and_bounds(
    tmp_path: Path,
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
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])
    restart_catalog()

    payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "bounds": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "value": 1,
        }
    )

    assert registry.validate_metric_payload("light_metric", payload) == payload

    info = registry.get_metric_info("light_metric")
    assert info is not None
    schema_defs = _json_object(info.request_schema["$defs"])
    assert "GeoJSONLocation" in schema_defs
    assert "GeoJSONBounds" in schema_defs
