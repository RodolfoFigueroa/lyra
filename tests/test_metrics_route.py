import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from lyra_app import registry
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import metrics


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "metrics": [
            {
                "name": "light_metric",
                "description": "A lightweight metric.",
                "request_schema": {
                    "type": "object",
                    "required": ["location", "value"],
                    "properties": {"location": {}, "value": {"type": "integer"}},
                },
                "spatial_inputs": {"location": "location"},
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
                "execution": {"queue": "lightweight"},
                "entrypoint": "fake_plugin.runner:run",
            }
        ],
    }


def _synced_repo(repo: Path) -> SyncedPluginRepo:
    entry = PluginRepoEntry(
        raw="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        owner="owner",
        repo="repo",
        ref=None,
    )
    return SyncedPluginRepo(entry=entry, path=repo, changed=False)


@pytest.fixture(autouse=True)
def reset_catalog() -> None:
    registry.reset_catalog()


def _use_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "sync_catalog_repos", lambda: [_synced_repo(repo)])


def test_metrics_route_returns_schema_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")

    _use_repo(repo, monkeypatch)
    response = asyncio.run(metrics.list_metrics())

    assert isinstance(response, list)
    assert len(response) == 1
    payload = response[0].model_dump()
    assert payload["name"] == "light_metric"
    assert payload["description"] == "A lightweight metric."
    assert payload["request_schema"]["required"] == ["location", "value"]
    assert payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in payload["request_schema"]["properties"]["location"]
    assert payload["output"]["kind"] == "table"
    assert payload["output"]["columns"][0]["name"] == "value"


def test_metric_route_returns_404_for_unknown_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    _use_repo(repo, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(metrics.list_metrics("missing"))

    assert exc_info.value.status_code == 404
