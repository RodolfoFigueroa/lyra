import json
import logging
from pathlib import Path
from typing import TypedDict

import pytest
from lyra.sdk.models import JobEnvelope

from lyra_app import auth, job_store
from lyra_app.config import LyraConfig, clear_config_cache, get_config, save_config
from lyra_app.db.connection import database_url
from lyra_app.db.redis import get_redis_url
from lyra_app.logging_config import configure_logging
from tests.config_helpers import load_test_config
from tests.redis_job_scripts import eval_job_script


class _FakeCredentialsValue:
    pass


class _EarthEngineCalls(TypedDict, total=False):
    service_account_file: Path
    scopes: list[str]
    credentials: _FakeCredentialsValue
    project: str


class FakeRedisSync:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: list[tuple[str, int]] = []
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}

    def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> None:
        if nx and key in self.values:
            return
        self.values[key] = value
        self.expirations.append((key, ex))

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def expire(self, key: str, ttl: int) -> None:
        self.expirations.append((key, ttl))

    def xadd(self, key: str, fields: dict[str, str]) -> str:
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, fields))
        return stream_id

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, key: str, min: str | float, max: float) -> None:  # ruff:ignore[builtin-argument-shadowing]
        lower = float("-inf") if min == "-inf" else float(min)
        sorted_set = self.sorted_sets.setdefault(key, {})
        for member, score in list(sorted_set.items()):
            if lower <= score <= max:
                sorted_set.pop(member, None)

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | float,
    ) -> int | str:
        del script
        return eval_job_script(self, numkeys, keys_and_args)


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


def test_job_store_uses_configured_ttl_seconds(tmp_path: Path) -> None:
    config = load_test_config(tmp_path)
    config_path = tmp_path / "config" / "lyra.toml"
    ttl_seconds = 123
    config = config.model_copy(
        update={
            "job_store": config.job_store.model_copy(
                update={"ttl_seconds": ttl_seconds}
            )
        }
    )
    _reload_test_config(config, config_path)
    redis = FakeRedisSync()

    try:
        job_store.create_job(
            JobEnvelope(job_id="job-ttl", metric="heavy_metric", input={"value": 1}),
            client=redis,
        )

        assert redis.expirations
        assert all(ttl == ttl_seconds for _key, ttl in redis.expirations)
    finally:
        clear_config_cache()
