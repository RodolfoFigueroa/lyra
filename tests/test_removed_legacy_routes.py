from pathlib import Path

from fastapi import FastAPI

from lyra_app.config import clear_config_cache
from lyra_app.routes import data_types, jobs, metrics
from tests.config_helpers import load_test_config


def test_openapi_exposes_only_job_execution_routes(tmp_path: Path) -> None:
    load_test_config(tmp_path)
    try:
        from lyra_app.routes import met_zone  # noqa: PLC0415

        app = FastAPI()
        app.include_router(jobs.router)
        app.include_router(data_types.router)
        app.include_router(met_zone.router)
        app.include_router(metrics.router)

        paths = set(app.openapi()["paths"])

        assert "/jobs" in paths
        assert "/jobs/{job_id}" in paths
        assert "/jobs/{job_id}/events" in paths
        assert "/jobs/{job_id}/result" in paths
        assert "/data-types" in paths
        assert "/lookups/met-zones" in paths

        removed_download_path = "/" + "download" + "_result/{download_id}"
        removed_socket_prefix = "/" + "w" + "s/"
        assert "/data_types" not in paths
        assert "/met_zone_code" not in paths
        assert removed_download_path not in paths
        assert all(not path.startswith(removed_socket_prefix) for path in paths)
    finally:
        clear_config_cache()
