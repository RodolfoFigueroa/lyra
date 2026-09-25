import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from lyra.sdk.types import JsonValue, validate_json_object

from lyra_app import registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.plugins import (
    MANIFEST_FILENAME,
)
from tests.catalog_helpers import configure_catalog_plugins, restart_catalog
from tests.config_helpers import load_test_config
from tests.contract_helpers import (
    FilterParameters,
    PositiveParameters,
    YearParameters,
)
from tests.contract_helpers import (
    metric_manifest as _metric,
)
from tests.plugin_helpers import plugin_config
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
)


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _manifest(
    *,
    plugin_name: str = "fake-plugin",
    metric: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 5,
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


def test_catalog_refresh_reads_v5_manifests_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins([repo])

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
    assert info_payload["request_schema"]["required"] == ["parameters", "location"]
    assert (
        info_payload["request_schema"]["$defs"]["ValueParameters"]["properties"][
            "value"
        ]["type"]
        == "integer"
    )
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]
    assert "GeoJSONLocation" in info_payload["request_schema"]["$defs"]
    assert entry is not None
    assert entry.queue == "lightweight"
    assert entry.distribution == "fake-plugin"


def test_metric_search_text_is_derived_from_public_catalog_fields(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
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
    configure_catalog_plugins([repo])
    restart_catalog()

    search_text = registry.get_metric_search_text("light_metric")

    assert search_text is not None
    assert "light_metric" in search_text
    assert "A metric." in search_text
    assert "location" in search_text
    assert "value" in search_text
    assert "Example input value." in search_text
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
    config.plugins.installed = [plugin_config(source)]

    def fail_import(name: str, package: str | None = None) -> object:  # ruff:ignore[unused-function-argument]
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    restart_catalog()
    entry = registry.get_metric_entry("light_metric")

    assert entry is not None
    assert entry.queue == "interactive"
    assert entry.distribution == "fake-plugin"


def test_catalog_refresh_loads_smoke_directory_fixture(tmp_path: Path) -> None:
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        plugins=[SMOKE_PLUGIN_DIR],
    )

    restart_catalog()
    metric_names = sorted(info.name for info in registry.get_metrics_info())
    table_entry = registry.get_metric_entry("smoke_table_metric")
    file_entry = registry.get_metric_entry("smoke_file_metric")
    table_info = registry.get_metric_info("smoke_table_metric")
    file_info = registry.get_metric_info("smoke_file_metric")

    assert metric_names == [
        "smoke_file_metric",
        "smoke_progress_metric",
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
        plugins=[missing_source],
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
    configure_catalog_plugins(
        [
            first_repo,
            second_repo,
        ]
    )

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_refresh_reads_v5_file_metric(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        name="raster_metric",
        description="A raster metric.",
        parameters=YearParameters,
        spatial={"bounds"},
        output={
            "kind": "file",
            "media_type": "image/tiff",
            "extensions": [".tif", ".tiff"],
        },
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_plugins([repo])

    restart_catalog()
    info = registry.get_metric_info("raster_metric")
    entry = registry.get_metric_entry("raster_metric")

    assert info is not None
    assert info.output.model_dump(mode="json") == {
        "kind": "file",
        "media_type": "image/tiff",
        "extensions": [".tif", ".tiff"],
    }
    assert info.request_schema["required"] == ["parameters", "bounds"]
    assert "GeoJSONBounds" in _json_object(info.request_schema["$defs"])
    assert entry is not None
    assert entry.queue == "heavy"


def test_catalog_refresh_rejects_invalid_request_json_schema(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric()
    metric["request_schema"]["properties"]["parameters"] = {
        "type": "not-a-json-schema-type"
    }
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_plugins([repo])

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_fingerprint_changes_only_when_manifest_content_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins([repo])

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
    configure_catalog_plugins([repo])

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
    configure_catalog_plugins([repo])

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
    configure_catalog_plugins([repo])
    restart_catalog()
    info = registry.get_metric_info("light_metric")
    assert info is not None
    first = registry.public_catalog_fingerprint([info])
    changed = info.model_copy(
        update={"spatial_inputs": {"renamed_location": "location"}},
    )

    assert registry.public_catalog_fingerprint([changed]) != first


def test_public_catalog_fingerprint_ignores_queue_assignment_and_manifest_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins([repo])
    config = get_config()

    restart_catalog()
    first = registry.get_public_catalog_fingerprint()
    config.plugins.installed[0].routing["light_metric"] = "batch"
    restart_catalog()
    after_queue_change = registry.get_public_catalog_fingerprint()

    assert after_queue_change == first

    configure_catalog_plugins(
        [
            repo,
        ]
    )
    restart_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_public_catalog_fingerprint_is_deterministic_across_plugin_load_order(
    tmp_path: Path,
) -> None:
    load_test_config(
        tmp_path,
        plugins=[],
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
        repo_a,
        repo_b,
    ]
    second_order = list(reversed(first_order))

    configure_catalog_plugins(first_order)
    restart_catalog()
    first = registry.get_public_catalog_fingerprint()

    configure_catalog_plugins(second_order)
    restart_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_validate_metric_payload_uses_manifest_json_schema(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    configure_catalog_plugins([repo])
    restart_catalog()

    payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "parameters": {"value": 1},
        }
    )
    assert registry.validate_metric_payload("light_metric", payload) == payload

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload(
            "light_metric",
            {
                "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
                "parameters": {"value": "wrong"},
            },
        )

    assert exc_info.value.errors[0]["type"] == "type"


def test_validate_metric_payload_uses_parameter_constraints(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(parameters=PositiveParameters)
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_plugins([repo])
    restart_catalog()

    valid_payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "parameters": {"value": 1},
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
                "parameters": {"value": -1},
            },
        )

    assert exc_info.value.errors[0]["type"] == "minimum"


@pytest.mark.parametrize(
    ("filters", "valid"),
    [(["retail", "retail"], True), ([1], False), (["x"] * 6, False)],
)
def test_validate_metric_payload_validates_ordinary_lists(
    tmp_path: Path, filters: list[Any], *, valid: bool
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest(metric=_metric(parameters=FilterParameters)))
    configure_catalog_plugins([repo])
    restart_catalog()
    payload: dict[str, Any] = {
        "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
        "parameters": {"sector_filters": filters},
    }
    if valid:
        assert registry.validate_metric_payload("light_metric", payload) == payload
    else:
        with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
            registry.validate_metric_payload("light_metric", payload)
        assert exc_info.value.errors[0]["loc"][:2] == ["parameters", "sector_filters"]


@pytest.mark.parametrize("version", [2, 4])
def test_catalog_refresh_rejects_legacy_manifest(
    version: int,
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
            "schema_version": version,
            "plugin": {"name": "legacy-plugin", "version": "1.0.0"},
            "metrics": [legacy_metric],
        },
    )
    configure_catalog_plugins([repo])

    with pytest.raises(registry.CatalogUnavailableError):
        restart_catalog()
    assert "Plugin catalog initialization failed" in caplog.text


def test_catalog_builds_spatial_schema_for_location_and_bounds(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    metric = _metric(
        spatial={"location", "bounds"},
    )
    _write_manifest(repo, _manifest(metric=metric))
    configure_catalog_plugins([repo])
    restart_catalog()

    payload = validate_json_object(
        {
            "location": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "bounds": {"data_type": "cvegeo_list", "value": ["090020001"]},
            "parameters": {"value": 1},
        }
    )

    assert registry.validate_metric_payload("light_metric", payload) == payload

    info = registry.get_metric_info("light_metric")
    assert info is not None
    schema_defs = _json_object(info.request_schema["$defs"])
    assert "GeoJSONLocation" in schema_defs
    assert "GeoJSONBounds" in schema_defs
