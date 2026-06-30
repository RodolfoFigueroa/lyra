from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from lyra_app.config import clear_config_cache
from lyra_app.registry import CatalogRefreshResult
from lyra_app.routes import admin
from tests.config_helpers import load_test_config


def test_require_admin_key_reads_configured_secret_file(tmp_path: Path) -> None:
    load_test_config(tmp_path)

    try:
        admin.require_admin_key(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-secret")
        )

        with pytest.raises(HTTPException) as exc_info:
            admin.require_admin_key(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
            )

        assert exc_info.value.status_code == 403
    finally:
        clear_config_cache()


def test_update_plugins_refreshes_catalog_and_restarts_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = CatalogRefreshResult(
        updated_plugins=["owner/repo"],
        previous_catalog_fingerprint="old",
        catalog_fingerprint="new",
        catalog_changed=True,
    )
    restarted: list[float] = []

    def restart_workers(*, timeout: float) -> None:
        restarted.append(timeout)

    monkeypatch.setattr(admin, "refresh_catalog", lambda: result)
    monkeypatch.setattr(admin, "graceful_worker_restart", restart_workers)

    response = admin.update_plugins(timeout=12.5)

    assert response.updated_plugins == ["owner/repo"]
    assert response.catalog_changed is True
    assert response.previous_catalog_fingerprint == "old"
    assert response.catalog_fingerprint == "new"
    assert restarted == [12.5]
