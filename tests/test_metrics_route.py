import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response

from lyra_app import registry
from lyra_app.config import clear_config_cache, get_config
from lyra_app.plugins import MANIFEST_FILENAME, PluginRepoEntry, SyncedPluginRepo
from lyra_app.routes import metrics
from tests.config_helpers import load_test_config, plugin_state_store


def _manifest() -> dict[str, Any]:
    return {
        "schema_version": 4,
        "plugin": {"name": "fake-plugin", "version": "1.0.0"},
        "factory": "fake_plugin.plugin:create_plugin",
        "metrics": [
            {
                "name": "light_metric",
                "description": "A lightweight metric.",
                "inputs": {
                    "location": {"kind": "location"},
                    "value": {"kind": "integer"},
                },
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
            }
        ],
    }


def _batched_manifest() -> dict[str, Any]:
    manifest = _manifest()
    metric = manifest["metrics"][0]
    metric["inputs"] = {
        "location": {"kind": "location"},
        "sector_filters": {
            "kind": "batch",
            "max_items": 20,
            "value": {"kind": "string", "min_length": 1, "max_length": 128},
            "label": True,
        },
    }
    metric["output"] = {
        "kind": "table",
        "columns": [],
        "batched_columns": [
            {
                "source": "sector_filters",
                "name": "job_accessibility_{key}",
                "type": "number",
                "unit": "jobs",
                "description": "Job accessibility for {label}.",
            }
        ],
    }
    return manifest


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
def reset_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    registry.reset_catalog()
    load_test_config(tmp_path, metric_queues={"light_metric": "lightweight"})
    monkeypatch.setattr(
        registry,
        "PluginStateStore",
        lambda *_args, **_kwargs: plugin_state_store(tmp_path, get_config()),
    )
    yield
    registry.reset_catalog()
    clear_config_cache()


def _use_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [_synced_repo(repo)],
    )


def test_metrics_route_returns_empty_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "sync_catalog_state_repos",
        lambda _config, _state: [],
    )
    response_context = Response()

    response = asyncio.run(metrics.list_metrics(response_context))

    payload = response.model_dump()
    assert payload["metrics"] == []
    assert payload["catalog_fingerprint"]
    assert response_context.headers["ETag"] == payload["catalog_fingerprint"]


def test_metrics_route_returns_schema_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")

    _use_repo(repo, monkeypatch)
    response_context = Response()
    response = asyncio.run(metrics.list_metrics(response_context))

    payload = response.model_dump()
    assert payload["catalog_fingerprint"]
    assert response_context.headers["ETag"] == payload["catalog_fingerprint"]
    assert len(payload["metrics"]) == 1
    metric_payload = payload["metrics"][0]
    assert metric_payload["name"] == "light_metric"
    assert metric_payload["description"] == "A lightweight metric."
    assert metric_payload["spatial_inputs"] == {"location": "location"}
    assert metric_payload["request_schema"]["required"] == ["location", "value"]
    assert metric_payload["request_schema"]["properties"]["value"] == {
        "type": "integer",
    }
    assert "oneOf" in metric_payload["request_schema"]["properties"]["location"]
    assert metric_payload["output"]["kind"] == "table"
    assert metric_payload["output"]["columns"][0]["name"] == "value"


def test_metric_route_returns_schema_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")

    _use_repo(repo, monkeypatch)
    response = asyncio.run(metrics.get_metric("light_metric"))

    payload = response.model_dump()
    assert payload["name"] == "light_metric"
    assert payload["description"] == "A lightweight metric."
    assert payload["spatial_inputs"] == {"location": "location"}
    assert payload["request_schema"]["required"] == ["location", "value"]
    assert payload["request_schema"]["properties"]["value"] == {"type": "integer"}
    assert "oneOf" in payload["request_schema"]["properties"]["location"]
    assert payload["output"]["kind"] == "table"
    assert payload["output"]["columns"][0]["name"] == "value"


def test_metrics_route_returns_batched_column_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_batched_manifest()),
        encoding="utf-8",
    )

    _use_repo(repo, monkeypatch)
    response = asyncio.run(metrics.get_metric("light_metric"))

    payload = response.model_dump()
    assert payload["output"]["kind"] == "table"
    assert payload["output"]["columns"] == []
    batched_column = payload["output"]["batched_columns"][0]
    assert set(batched_column) == {
        "source",
        "name",
        "type",
        "unit",
        "description",
        "nullable",
    }
    assert batched_column["source"] == "sector_filters"
    assert batched_column["name"] == "job_accessibility_{key}"
    assert "oneOf" in payload["request_schema"]["properties"]["location"]
    assert payload["request_schema"]["properties"]["sector_filters"]["maxItems"] == 20


def test_metric_route_returns_404_for_unknown_metric(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(json.dumps(_manifest()), encoding="utf-8")
    _use_repo(repo, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(metrics.get_metric("missing"))

    assert exc_info.value.status_code == 404
