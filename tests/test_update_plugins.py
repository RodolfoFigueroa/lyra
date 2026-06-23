import pytest

from lyra_app.registry import CatalogRefreshResult
from lyra_app.routes import admin


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
