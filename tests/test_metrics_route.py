import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response

from lyra_app import registry
from lyra_app.config import clear_config_cache
from lyra_app.plugins import MANIFEST_FILENAME, PluginLocation
from lyra_app.routes import metrics
from tests.catalog_helpers import configure_catalog_sources
from tests.config_helpers import load_test_config
from tests.contract_helpers import FilterParameters, metric_manifest, plugin_manifest


def _manifest() -> dict[str, Any]:
    return plugin_manifest(
        metric_manifest(name="light_metric", description="A lightweight metric.")
    )


def _list_manifest() -> dict[str, Any]:
    return plugin_manifest(
        metric_manifest(name="light_metric", parameters=FilterParameters)
    )


@pytest.fixture(autouse=True)
def reset_catalog(tmp_path: Path) -> Iterator[None]:
    registry.reset_catalog()
    load_test_config(tmp_path, metric_queues={"light_metric": "lightweight"})
    yield
    registry.reset_catalog()
    clear_config_cache()


def _use_repo(repo: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    configure_catalog_sources([PluginLocation(repo_id="repo", path=repo)])


def test_metrics_route_returns_empty_catalog() -> None:
    configure_catalog_sources([])
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
    assert metric_payload["request_schema"]["required"] == ["parameters", "location"]
    assert (
        metric_payload["request_schema"]["$defs"]["ValueParameters"]["properties"][
            "value"
        ]["type"]
        == "integer"
    )
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
    assert payload["request_schema"]["required"] == ["parameters", "location"]
    assert (
        payload["request_schema"]["$defs"]["ValueParameters"]["properties"]["value"][
            "type"
        ]
        == "integer"
    )
    assert "oneOf" in payload["request_schema"]["properties"]["location"]
    assert payload["output"]["kind"] == "table"
    assert payload["output"]["columns"][0]["name"] == "value"


def test_metrics_route_returns_list_schema_and_static_columns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / MANIFEST_FILENAME).write_text(
        json.dumps(_list_manifest()),
        encoding="utf-8",
    )

    _use_repo(repo, monkeypatch)
    response = asyncio.run(metrics.get_metric("light_metric"))

    payload = response.model_dump()
    assert payload["output"]["kind"] == "table"
    assert payload["output"]["columns"][0]["name"] == "value"
    assert "batched_columns" not in payload["output"]
    filters = payload["request_schema"]["$defs"]["FilterParameters"]["properties"][
        "sector_filters"
    ]
    assert filters["maxItems"] == 5
    assert filters["items"] == {"type": "string"}


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
