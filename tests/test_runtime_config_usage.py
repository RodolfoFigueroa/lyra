import json
import logging
from pathlib import Path
from typing import TypedDict

import pytest

from lyra_app import auth
from lyra_app.config import LyraConfig, clear_config_cache, get_config
from lyra_app.db.connection import database_url
from lyra_app.db.redis import get_redis_url
from lyra_app.logging_config import configure_logging
from tests.config_helpers import load_test_config
from tests.config_serialization import save_config


class _FakeCredentialsValue:
    pass


class _EarthEngineCalls(TypedDict, total=False):
    service_account_file: Path
    scopes: list[str]
    credentials: _FakeCredentialsValue
    project: str


def _reload_test_config(config: LyraConfig, config_path: Path) -> None:
    save_config(config, config_path)
    clear_config_cache()
    get_config(config_path)


def test_redis_url_uses_loaded_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    config_path = tmp_path / "config" / "lyra.toml"
    configured_url = "redis://configured-redis:6380/2"
    config = config.model_copy(
        update={"redis": config.redis.model_copy(update={"url": configured_url})}
    )
    _reload_test_config(config, config_path)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://env-redis:6379/0")

    assert get_redis_url() == configured_url


def test_database_url_uses_env_backed_config(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)

    url = database_url(config=config)

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "lyra"
    assert url.password == config.database.password
    assert url.host == "postgres"
    assert url.port == 5432
    assert url.database == "lyra"


def test_initialize_earth_engine_uses_configured_file_and_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_test_config(tmp_path)
    calls: _EarthEngineCalls = {}
    credentials = _FakeCredentialsValue()

    class FakeCredentials:
        @staticmethod
        def from_service_account_file(
            path: Path,
            *,
            scopes: list[str],
        ) -> _FakeCredentialsValue:
            calls["service_account_file"] = path
            calls["scopes"] = scopes
            return credentials

    def initialize(
        credentials_value: _FakeCredentialsValue,
        *,
        project: str,
    ) -> None:
        calls["credentials"] = credentials_value
        calls["project"] = project

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)
    monkeypatch.setattr(auth.ee, "Initialize", initialize)

    auth.initialize_earth_engine(config)

    assert calls == {
        "service_account_file": tmp_path / "secrets" / "service-account.json",
        "scopes": ["https://www.googleapis.com/auth/earthengine"],
        "credentials": credentials,
        "project": "earth-engine-project",
    }


def test_configure_logging_uses_configured_level_and_file(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    log_file = tmp_path / "logs" / "runtime.log"
    config = config.model_copy(
        update={
            "logging": config.logging.model_copy(
                update={"level": "DEBUG", "file": log_file}
            )
        }
    )
    logger = logging.getLogger("lyra_app")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()

    try:
        configured = configure_logging(config)
        handler = configured.handlers[0]
        configured.info("runtime config log")
        handler.flush()

        assert configured.level == logging.DEBUG
        assert isinstance(handler, logging.FileHandler)
        payload = json.loads(log_file.read_text(encoding="utf-8"))
        assert payload["level"] == "INFO"
        assert payload["logger"] == "lyra_app"
        assert payload["message"] == "runtime config log"
        assert payload["timestamp"].endswith("+00:00")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate
