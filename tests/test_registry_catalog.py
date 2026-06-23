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
    queue: str = "lightweight",
    entrypoint: str = "fake_plugin.runner:run",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "request_schema": request_schema
        or {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        },
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


def _legacy_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "light_metric",
                "description": "A metric.",
                "parameters": [
                    {
                        "name": "value",
                        "type": "int",
                        "required": True,
                        "default": None,
                    }
                ],
                "returns_file": False,
                "tavi_hint": "",
                "request_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                    "additionalProperties": False,
                },
                "execution": {
                    "profile": "lightweight",
                    "queue": "lightweight",
                    "timeout_seconds": 30,
                },
                "callable": {"mode": "single", "calculate": "fake_plugin:calculate"},
            }
        ],
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
    assert info.model_dump() == {
        "name": "light_metric",
        "description": "A metric.",
        "request_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer"}},
            "additionalProperties": False,
        },
        "result_schema": None,
    }
    assert entry is not None
    assert entry.queue == "lightweight"
    assert entry.entrypoint == "fake_plugin.runner:run"


def test_catalog_refresh_rejects_legacy_v1_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_manifest(repo, _legacy_manifest())
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])

    with pytest.raises(RuntimeError, match=r"Plugin manifest .* is invalid"):
        registry.refresh_catalog()


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

    assert registry.validate_metric_payload("light_metric", {"value": 1}) == {
        "value": 1
    }

    with pytest.raises(registry.MetricPayloadValidationError) as exc_info:
        registry.validate_metric_payload("light_metric", {"value": "wrong"})

    assert exc_info.value.errors[0]["type"] == "type"
