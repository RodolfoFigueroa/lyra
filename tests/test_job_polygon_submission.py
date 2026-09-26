"""Reject explicit Point inputs before accessing database or job queue."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from lyra.sdk.models.job import JobCreateRequest

from lyra_app import job_store, registry
from lyra_app.config import clear_config_cache
from lyra_app.db.connection import ApplicationDatabaseRuntime
from lyra_app.plugins import MANIFEST_FILENAME
from lyra_app.routes.jobs import create_job
from tests.catalog_helpers import configure_catalog_plugins
from tests.config_helpers import load_test_config
from tests.contract_helpers import metric_manifest, plugin_manifest
from tests.smoke_plugin_helpers import feature_collection


@pytest.mark.parametrize("spatial_name", ["location", "bounds"])
def test_job_rejects_point_before_enqueue(
    spatial_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path, metric_queues={"light_metric": "lightweight"})
    registry.reset_catalog()
    repo = tmp_path / "plugin"
    repo.mkdir()
    manifest = plugin_manifest(metric_manifest(spatial={"location", "bounds"}))
    (repo / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    configure_catalog_plugins([repo])
    enqueue = Mock()
    monkeypatch.setattr(job_store, "enqueue", enqueue)
    database = Mock(spec=ApplicationDatabaseRuntime)
    database.config = config
    point = feature_collection(("point",))
    point["features"][0]["geometry"] = {"type": "Point", "coordinates": [-99, 19]}
    inputs = {
        "location": {"data_type": "geojson", "value": feature_collection()},
        "bounds": {"data_type": "geojson", "value": feature_collection()},
        "parameters": {"value": 1},
    }
    inputs[spatial_name] = {"data_type": "geojson", "value": point}
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                create_job(
                    JobCreateRequest(metric="light_metric", input=inputs),
                    database=database,
                )
            )
        assert error.value.status_code == 422
        assert spatial_name in str(error.value.detail)
        enqueue.assert_not_called()
        database.require_spatial_engine.assert_not_called()
        database.run_spatial.assert_not_called()
    finally:
        registry.reset_catalog()
        clear_config_cache()
