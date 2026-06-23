import importlib
import json
from pathlib import Path

import pytest

from lyra_app import registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo


def _manifest(description: str = "A metric.") -> dict:
    return {
        "schema_version": 1,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "light_metric",
                "description": description,
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


def _write_manifest(repo: Path, manifest: dict) -> None:
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


def test_catalog_refresh_reads_manifests_without_importing_plugin_code(
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

    assert result.catalog_changed is True
    assert registry.get_metric_info("light_metric", prettify_types=True) is not None


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
        json.dumps(_manifest("A changed metric.")),
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
