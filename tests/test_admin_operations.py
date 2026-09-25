from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from lyra_app.config import clear_config_cache
from lyra_app.registry import reset_catalog
from lyra_app.routes import admin
from tests.config_helpers import load_test_config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def admin_context(
    tmp_path: Path,
) -> Iterator[Path]:
    reset_catalog()
    load_test_config(tmp_path)
    yield tmp_path
    reset_catalog()
    clear_config_cache()


def _assert_http_error(
    status_code: int,
    func: Callable[..., object],
    *args: object,
) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        func(*args)
    assert exc_info.value.status_code == status_code
    return exc_info.value


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


def test_admin_router_requires_bearer_key_for_all_routes() -> None:
    assert admin.router.prefix == "/admin"
    assert any(
        dependency.dependency is admin.require_admin_key
        for dependency in admin.router.dependencies
    )
