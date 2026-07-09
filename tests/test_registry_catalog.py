import importlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lyra_app import registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.plugin_state import PluginState, make_repo_record
from lyra_app.plugins import (
    MANIFEST_FILENAME,
    PluginRepoEntry,
    PluginSyncError,
    SyncedPluginRepo,
)
from tests.config_helpers import load_test_config, plugin_state_store
from tests.smoke_plugin_helpers import (
    SMOKE_METRIC_QUEUES,
    SMOKE_PLUGIN_DIR,
    directory_uri,
    smoke_plugin_uri,
)


def _metric(
    *,
    name: str = "light_metric",
    description: str = "A metric.",
    inputs: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
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


def _synced_repo(
    repo: Path,
    *,
    changed: bool = False,
    raw: str = "owner/repo",
    repo_name: str = "repo",
    ref: str | None = None,
) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw=raw,
        clone_url=f"https://github.com/owner/{repo_name}.git",
        owner="owner",
        repo=repo_name,
        ref=ref,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=changed)


@pytest.fixture(autouse=True)
def reset_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    registry.reset_catalog()
    load_test_config(
        tmp_path,
        metric_queues={
            "light_metric": "lightweight",
            "raster_metric": "heavy",
        },
    )
    monkeypatch.setattr(
        registry,
        "PluginStateStore",
        lambda *_args, **_kwargs: plugin_state_store(tmp_path, get_config()),
    )
    yield
    registry.reset_catalog()
    clear_config_cache()


def test_catalog_refresh_reads_v3_manifests_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

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
    assert info_payload["spatial_inputs"] == {"location": "location"}
    assert info_payload["output"]["kind"] == "table"
    assert info_payload["output"]["columns"][0]["name"] == "value"
    assert info_payload["request_schema"]["required"] == ["location", "value"]
    assert info_payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in info_payload["request_schema"]["properties"]["location"]
    assert "GeoJSONLocationWrapperV3" in info_payload["request_schema"]["$defs"]
    assert entry is not None
    assert entry.queue == "lightweight"
    assert entry.repo_id == "owner__repo"
    assert entry.entrypoint == "fake_plugin.runner:run"


def test_metric_search_text_is_derived_from_public_catalog_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    registry.refresh_catalog()

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


def test_catalog_sync_uses_enabled_state_repos_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = get_config()
    directory_source = tmp_path / "directory-plugin"
    state = PluginState(
        repos=[
            make_repo_record("owner/enabled-plugin@main"),
            make_repo_record(f"dir://{directory_source}", repo_id="directory-plugin"),
            make_repo_record(
                "owner/disabled-plugin@v1.0.0",
                repo_id="disabled-plugin",
                enabled=False,
            ),
        ],
    )
    calls: list[tuple[Path, list[str], bool]] = []

    def sync_repos(
        target_dir: Path,
        raw_entries: list[str],
        *,
        raise_on_error: bool,
    ) -> list[SyncedPluginRepo]:
        calls.append((target_dir, raw_entries, raise_on_error))
        return []

    monkeypatch.setattr(registry, "sync_plugin_repos", sync_repos)

    synced = registry.sync_catalog_state_repos(config, state)

    assert synced == []
    assert calls == [
        (
            tmp_path / "plugins" / "catalog",
            [
                "owner/enabled-plugin@main",
                f"dir://{directory_source.resolve().as_posix()}",
            ],
            True,
        )
    ]


def test_catalog_refresh_reads_directory_source_without_importing_plugin_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "directory-plugin"
    _write_manifest(source, _manifest())
    store = plugin_state_store(tmp_path, get_config())
    store.delete_repo("owner__repo")
    store.add_repo(
        f"dir://{source}",
        repo_id="directory-plugin",
    )

    def fail_import(name: str, package: str | None = None) -> object:  # noqa: ARG001
        msg = "API catalog loading must not import plugin code"
        raise AssertionError(msg)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    result = registry.refresh_catalog()
    entry = registry.get_metric_entry("light_metric")

    assert result.updated_plugins == [f"dir:{source.resolve()}"]
    assert entry is not None
    assert entry.queue == "interactive"
    assert entry.repo_id == "directory-plugin"
    assert entry.entrypoint == "fake_plugin.runner:run"


def test_catalog_refresh_loads_smoke_directory_fixture(tmp_path: Path) -> None:
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[smoke_plugin_uri()],
    )

    result = registry.refresh_catalog()
    metric_names = sorted(info.name for info in registry.get_metrics_info())
    table_entry = registry.get_metric_entry("smoke_table_metric")
    file_entry = registry.get_metric_entry("smoke_file_metric")
    table_info = registry.get_metric_info("smoke_table_metric")
    file_info = registry.get_metric_info("smoke_file_metric")

    assert result.updated_plugins == [f"dir:{SMOKE_PLUGIN_DIR.resolve()}"]
    assert metric_names == [
        "smoke_cancel_metric",
        "smoke_file_metric",
        "smoke_table_metric",
    ]
    assert table_entry is not None
    assert table_entry.queue == "interactive"
    assert "GeoJSONLocationWrapperV3" in table_entry.request_schema["$defs"]
    assert table_info is not None
    assert table_info.spatial_inputs == {"location": "location"}
    assert file_entry is not None
    assert file_entry.metric.output.kind == "file"
    assert file_info is not None
    assert file_info.spatial_inputs == {"location": "location"}


def test_catalog_refresh_detects_smoke_directory_manifest_edits(
    tmp_path: Path,
) -> None:
    source = tmp_path / "editable-smoke-plugin"
    shutil.copytree(SMOKE_PLUGIN_DIR, source)
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[directory_uri(source)],
    )
    first = registry.refresh_catalog()
    manifest_path = source / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    edited_description = "Edited smoke table metric description."
    for metric in manifest["metrics"]:
        if metric["name"] == "smoke_table_metric":
            metric["description"] = edited_description
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    second = registry.refresh_catalog()
    info = registry.get_metric_info("smoke_table_metric")

    assert first.catalog_changed is True
    assert second.updated_plugins == [f"dir:{source.resolve()}"]
    assert second.previous_catalog_fingerprint == first.catalog_fingerprint
    assert second.catalog_changed is True
    assert info is not None
    assert info.description == edited_description


def test_catalog_refresh_reports_unchanged_smoke_directory_source(
    tmp_path: Path,
) -> None:
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[smoke_plugin_uri()],
    )
    first = registry.refresh_catalog()

    second = registry.refresh_catalog()

    assert first.updated_plugins == [f"dir:{SMOKE_PLUGIN_DIR.resolve()}"]
    assert second.updated_plugins == []
    assert second.previous_catalog_fingerprint == first.catalog_fingerprint
    assert second.catalog_fingerprint == first.catalog_fingerprint
    assert second.catalog_changed is False


def test_catalog_refresh_rejects_missing_directory_source(tmp_path: Path) -> None:
    missing_source = tmp_path / "missing-smoke-plugin"
    load_test_config(
        tmp_path,
        metric_queues=SMOKE_METRIC_QUEUES,
        repos=[directory_uri(missing_source)],
    )

    with pytest.raises(PluginSyncError, match="does not exist"):
        registry.refresh_catalog()


def test_catalog_refresh_syncs_enabled_repos_from_plugin_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    store = plugin_state_store(tmp_path, get_config())
    store.delete_repo("owner__repo")
    store.add_repo(
        "owner/example-plugin@main",
        repo_id="example",
    )
    store.set_metric_queue("light_metric", "lightweight", repo_id="example")
    calls: list[tuple[Path, list[str], bool]] = []

    def sync_repos(
        target_dir: Path,
        raw_entries: list[str],
        *,
        raise_on_error: bool,
    ) -> list[SyncedPluginRepo]:
        calls.append((target_dir, raw_entries, raise_on_error))
        return [
            _synced_repo(
                repo,
                raw="owner/example-plugin@main",
                repo_name="example-plugin",
                ref="main",
            )
        ]

    monkeypatch.setattr(registry, "sync_plugin_repos", sync_repos)

    registry.refresh_catalog()

    entry = registry.get_metric_entry("light_metric")
    assert calls == [
        (tmp_path / "plugins" / "catalog", ["owner/example-plugin@main"], True)
    ]
    assert entry is not None
    assert entry.queue == "lightweight"


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
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(first_repo), _synced_repo(second_repo)],
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
        entrypoint="fake_plugin.runner:run_raster",
    )
    _write_manifest(repo, _manifest(metric=metric))
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

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


def test_catalog_refresh_auto_assigns_new_metric_queue_to_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest(metric=_metric(name="new_metric")))
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

    registry.refresh_catalog()

    persisted = plugin_state_store(tmp_path, get_config()).load()
    entry = registry.get_metric_entry("new_metric")
    assert persisted.metric_queues["new_metric"].queue == "interactive"
    assert persisted.metric_queues["new_metric"].repo_id == "owner__repo"
    assert entry is not None
    assert entry.queue == "interactive"
    assert entry.repo_id == "owner__repo"


def test_catalog_refresh_recreates_deleted_metric_route_with_default_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    store = plugin_state_store(tmp_path, get_config())

    assert store.delete_metric_queue("light_metric") is True

    result = registry.refresh_catalog()

    persisted = store.load()
    entry = registry.get_metric_entry("light_metric")
    assert result.assigned_metric_queues == ["light_metric"]
    assert persisted.metric_queues["light_metric"].queue == "interactive"
    assert persisted.metric_queues["light_metric"].repo_id == "owner__repo"
    assert entry is not None
    assert entry.queue == "interactive"


def test_catalog_refresh_removes_stale_metric_queue_assignments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_test_config(
        tmp_path,
        metric_queues={
            "light_metric": "lightweight",
            "removed_metric": "heavy",
        },
    )
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

    result = registry.refresh_catalog()

    persisted = plugin_state_store(tmp_path, get_config()).load()
    entry = registry.get_metric_entry("light_metric")
    assert result.removed_metric_queues == ["removed_metric"]
    assert "removed_metric" not in persisted.metric_queues
    assert entry is not None
    assert entry.queue == "lightweight"
    assert registry.get_metric_entry("removed_metric") is None


def test_catalog_refresh_keeps_previous_registry_when_assignment_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_repo = tmp_path / "repo-1"
    second_repo = tmp_path / "repo-2"
    _write_manifest(first_repo, _manifest())
    _write_manifest(second_repo, _manifest(metric=_metric(name="new_metric")))
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(first_repo)],
    )
    registry.refresh_catalog()
    original_entry = registry.get_metric_entry("light_metric")

    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(second_repo)],
    )
    store = plugin_state_store(tmp_path, get_config())

    def fail_assignment(*_args: Any, **_kwargs: Any) -> object:
        msg = "disk full"
        raise RuntimeError(msg)

    monkeypatch.setattr(store, "sync_metric_queues", fail_assignment)
    monkeypatch.setattr(registry, "PluginStateStore", lambda *_args, **_kwargs: store)

    with pytest.raises(RuntimeError, match="disk full"):
        registry.refresh_catalog()

    assert registry.get_metric_entry("light_metric") == original_entry
    assert registry.get_metric_entry("new_metric") is None


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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

    with pytest.raises(RuntimeError, match=r"Plugin manifest .* is invalid"):
        registry.refresh_catalog()


def test_catalog_fingerprint_changes_only_when_manifest_content_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

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


def test_public_catalog_fingerprint_changes_when_public_contract_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

    registry.refresh_catalog()
    first = registry.get_public_catalog_fingerprint()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(metric=_metric(description="A changed metric."))),
        encoding="utf-8",
    )
    registry.refresh_catalog()

    assert registry.get_public_catalog_fingerprint() != first


def test_public_catalog_fingerprint_includes_spatial_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    registry.refresh_catalog()
    info = registry.get_metric_info("light_metric")
    assert info is not None
    first = registry.public_catalog_fingerprint([info])
    changed = info.model_copy(
        update={"spatial_inputs": {"renamed_location": "location"}},
    )

    assert registry.public_catalog_fingerprint([changed]) != first


def test_public_catalog_fingerprint_ignores_queue_assignment_and_repo_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    store = plugin_state_store(tmp_path, get_config())

    registry.refresh_catalog()
    first = registry.get_public_catalog_fingerprint()
    store.set_metric_queue("light_metric", "batch", repo_id="owner__repo")
    registry.refresh_catalog()
    after_queue_change = registry.get_public_catalog_fingerprint()

    assert after_queue_change == first

    store.delete_repo("owner__repo")
    store.add_repo("owner/renamed", repo_id="custom-repo")
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [
            _synced_repo(repo, raw="owner/renamed", repo_name="renamed"),
        ],
    )
    registry.refresh_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_public_catalog_fingerprint_is_deterministic_across_plugin_load_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        _synced_repo(repo_a, raw="owner/repo-a", repo_name="repo-a"),
        _synced_repo(repo_b, raw="owner/repo-b", repo_name="repo-b"),
    ]
    second_order = list(reversed(first_order))

    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: first_order,
    )
    registry.refresh_catalog()
    first = registry.get_public_catalog_fingerprint()

    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: second_order,
    )
    registry.refresh_catalog()

    assert registry.get_public_catalog_fingerprint() == first


def test_validate_metric_payload_uses_manifest_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _manifest())
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
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


def test_validate_metric_payload_rejects_duplicate_batch_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    registry.refresh_catalog()

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
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
    registry.refresh_catalog()

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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )

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
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )
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
