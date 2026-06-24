from fastapi import FastAPI

from lyra_app.routes import data_types, jobs, metrics


def test_openapi_exposes_only_job_execution_routes() -> None:
    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(data_types.router)
    app.include_router(metrics.router)

    paths = set(app.openapi()["paths"])

    assert "/jobs" in paths
    assert "/jobs/{job_id}" in paths
    assert "/jobs/{job_id}/events" in paths
    assert "/jobs/{job_id}/result" in paths

    removed_download_path = "/" + "download" + "_result/{download_id}"
    removed_socket_prefix = "/" + "w" + "s/"
    assert removed_download_path not in paths
    assert all(not path.startswith(removed_socket_prefix) for path in paths)
