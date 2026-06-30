from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from lyra_app.config import (
    DEFAULT_ADMIN_API_KEY_FILE,
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_DATABASE_PASSWORD_FILE,
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_JOB_STORE_TTL_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    LYRA_DATA_DIR,
    ConfigSecretError,
    LyraConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_secrets(base: Path) -> dict[str, Path]:
    secrets_dir = base / "secrets"
    secrets_dir.mkdir()
    paths = {
        "postgres_password": secrets_dir / "postgres_password",
        "admin_api_key": secrets_dir / "admin_api_key",
        "service_account": secrets_dir / "service-account.json",
    }
    paths["postgres_password"].write_text("  postgres-secret\n", encoding="utf-8")
    paths["admin_api_key"].write_text("\nadmin-secret  ", encoding="utf-8")
    paths["service_account"].write_text("{}", encoding="utf-8")
    return paths


def _valid_config(base: Path) -> dict[str, Any]:
    secret_paths = _write_secrets(base)
    return {
        "schema_version": 1,
        "api": {
            "host": "0.0.0.0",
            "port": 5219,
        },
        "redis": {
            "url": "redis://redis:6379/0",
        },
        "database": {
            "host": "postgres",
            "port": 5432,
            "name": "lyra",
            "user": "lyra",
            "password_file": str(secret_paths["postgres_password"]),
        },
        "earth_engine": {
            "project": "earth-engine-project",
            "service_account_file": str(secret_paths["service_account"]),
        },
        "admin": {
            "api_key_file": str(secret_paths["admin_api_key"]),
        },
        "logging": {
            "level": "INFO",
            "file": str(base / "logs" / "lyra.log"),
        },
        "job_store": {
            "ttl_seconds": 600,
        },
        "plugins": {
            "catalog_dir": str(base / "plugins" / "catalog"),
            "runner_base_dir": str(base / "plugins" / "runners"),
            "default_queue": "interactive",
            "allowed_queues": ["interactive", "batch"],
        },
        "workers": {
            "interactive": {
                "queues": ["interactive"],
                "concurrency": 32,
                "install_dir": str(base / "plugins" / "runners" / "interactive"),
                "temp_dir": str(base / "cache" / "jobs" / "interactive"),
            },
            "batch": {
                "queues": ["batch"],
                "concurrency": 8,
                "install_dir": str(base / "plugins" / "runners" / "batch"),
                "temp_dir": str(base / "cache" / "jobs" / "batch"),
            },
        },
    }


def _assert_invalid(raw: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        LyraConfig.model_validate(raw)


def test_config_contract_accepts_complete_schema(tmp_path: Path) -> None:
    config = LyraConfig.model_validate(_valid_config(tmp_path))

    assert config.schema_version == 1
    assert config.api.host == "0.0.0.0"
    assert config.api.port == 5219
    assert config.redis.url == "redis://redis:6379/0"
    assert config.database.password_file == tmp_path / "secrets" / "postgres_password"
    assert config.earth_engine.service_account_file == (
        tmp_path / "secrets" / "service-account.json"
    )
    assert config.admin.api_key_file == tmp_path / "secrets" / "admin_api_key"
    assert config.logging.level == "INFO"
    assert config.logging.file == tmp_path / "logs" / "lyra.log"
    assert config.job_store.ttl_seconds == 600
    assert config.plugins.allowed_queues == ["interactive", "batch"]
    assert config.get_worker("interactive").concurrency == 32


def test_config_contract_applies_documented_field_defaults(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["api"] = {}
    del raw["database"]["password_file"]
    del raw["earth_engine"]["service_account_file"]
    raw["admin"] = {}
    raw["logging"] = {}
    raw["job_store"] = {}
    del raw["plugins"]["catalog_dir"]
    del raw["plugins"]["runner_base_dir"]
    raw["workers"]["interactive"] = {"queues": ["interactive"]}

    config = LyraConfig.model_validate(raw)

    assert config.api.host == DEFAULT_API_HOST
    assert config.api.port == DEFAULT_API_PORT
    assert config.database.password_file == DEFAULT_DATABASE_PASSWORD_FILE
    assert config.earth_engine.service_account_file == (
        DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
    )
    assert config.admin.api_key_file == DEFAULT_ADMIN_API_KEY_FILE
    assert config.logging.level == DEFAULT_LOG_LEVEL
    assert config.logging.file is None
    assert config.job_store.ttl_seconds == DEFAULT_JOB_STORE_TTL_SECONDS
    assert config.plugins.catalog_dir == DEFAULT_PLUGIN_CATALOG_DIR
    assert config.plugins.runner_base_dir == DEFAULT_PLUGIN_RUNNER_BASE_DIR
    assert config.get_worker("interactive").concurrency == 1
    assert config.worker_install_dir("interactive") == (
        DEFAULT_PLUGIN_RUNNER_BASE_DIR / "interactive"
    )
    assert config.worker_temp_dir("interactive") == (
        LYRA_DATA_DIR / "cache" / "jobs" / "interactive"
    )


def test_config_contract_rejects_unknown_fields(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["unexpected"] = True

    _assert_invalid(raw, "Extra inputs are not permitted")


def test_config_contract_rejects_unknown_nested_fields(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["plugins"]["extra"] = "surprise"

    _assert_invalid(raw, "Extra inputs are not permitted")


def test_config_contract_requires_known_schema_version(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["schema_version"] = 2

    _assert_invalid(raw, "Input should be 1")


@pytest.mark.parametrize(
    ("section", "field", "value", "match"),
    [
        ("api", "port", 0, "greater than or equal to 1"),
        ("redis", "url", "postgres://db:5432/lyra", "redis.url"),
        ("database", "port", 70000, "less than or equal to 65535"),
        ("logging", "level", "NOPE", "logging.level"),
        ("job_store", "ttl_seconds", 0, "greater than 0"),
    ],
)
def test_config_contract_rejects_invalid_values(
    tmp_path: Path,
    section: str,
    field: str,
    value: Any,
    match: str,
) -> None:
    raw = _valid_config(tmp_path)
    raw[section][field] = value

    _assert_invalid(raw, match)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("database", "password_file"),
        ("earth_engine", "service_account_file"),
        ("admin", "api_key_file"),
        ("logging", "file"),
        ("plugins", "catalog_dir"),
        ("plugins", "runner_base_dir"),
    ],
)
def test_config_contract_requires_absolute_paths(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    raw = _valid_config(tmp_path)
    raw[section][field] = "relative/path"

    _assert_invalid(raw, "path must be absolute")


def test_config_contract_trims_queues(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["plugins"]["allowed_queues"] = [" interactive ", " batch "]
    raw["plugins"]["default_queue"] = " interactive "
    raw["workers"] = {" interactive ": {"queues": [" interactive "]}}

    config = LyraConfig.model_validate(raw)

    assert config.plugins.allowed_queues == ["interactive", "batch"]
    assert config.plugins.default_queue == "interactive"
    assert sorted(config.workers) == ["interactive"]


def test_config_contract_rejects_plugin_repos_field(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["plugins"]["repos"] = ["owner/plugin-a"]

    _assert_invalid(raw, "Extra inputs are not permitted")


def test_config_contract_rejects_default_queue_outside_allowed_queues(
    tmp_path: Path,
) -> None:
    raw = _valid_config(tmp_path)
    raw["plugins"]["default_queue"] = "priority"

    _assert_invalid(raw, "plugins.default_queue")


def test_config_contract_rejects_plugin_metric_queues_field(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["plugins"]["metric_queues"] = {"walkability_score": "interactive"}

    _assert_invalid(raw, "Extra inputs are not permitted")


def test_config_contract_rejects_worker_queue_outside_allowed_queues(
    tmp_path: Path,
) -> None:
    raw = _valid_config(tmp_path)
    raw["workers"]["interactive"]["queues"] = ["priority"]

    _assert_invalid(raw, "workers.<name>.queues values")


def test_config_contract_reads_scalar_secret_references(tmp_path: Path) -> None:
    config = LyraConfig.model_validate(_valid_config(tmp_path))

    assert config.database.read_password() == "postgres-secret"
    assert config.admin.read_api_key() == "admin-secret"


def test_config_contract_reports_missing_scalar_secret_file(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    missing_file = tmp_path / "secrets" / "missing"
    raw["admin"]["api_key_file"] = str(missing_file)
    config = LyraConfig.model_validate(raw)

    with pytest.raises(ConfigSecretError, match=r"admin\.api_key_file"):
        config.admin.read_api_key()


def test_config_contract_reports_empty_scalar_secret_file(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    empty_file = tmp_path / "secrets" / "empty"
    empty_file.write_text(" \n", encoding="utf-8")
    raw["database"]["password_file"] = str(empty_file)
    config = LyraConfig.model_validate(raw)

    with pytest.raises(ConfigSecretError, match="secret file is empty"):
        config.database.read_password()


def test_config_contract_rejects_empty_required_sections(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    del raw["redis"]

    _assert_invalid(raw, "Field required")


def test_config_contract_input_is_not_mutated(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    original = deepcopy(raw)

    LyraConfig.model_validate(raw)

    assert raw == original
