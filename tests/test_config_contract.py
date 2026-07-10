from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from lyra_app.config import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_JOB_STORE_TTL_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MCP_MOUNT_PATH,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_DATA_DIR,
    LYRA_POSTGRES_DB_ENV,
    LYRA_POSTGRES_HOST_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LYRA_POSTGRES_PORT_ENV,
    LYRA_POSTGRES_USER_ENV,
    ConfigSecretError,
    LyraConfig,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_secrets(base: Path) -> dict[str, Path]:
    secrets_dir = base / "secrets"
    secrets_dir.mkdir()
    paths = {
        "service_account": secrets_dir / "service-account.json",
    }
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
        "earth_engine": {
            "project": "earth-engine-project",
            "service_account_file": str(secret_paths["service_account"]),
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


@pytest.fixture(autouse=True)
def _runtime_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LYRA_POSTGRES_HOST_ENV, " postgres ")
    monkeypatch.setenv(LYRA_POSTGRES_PORT_ENV, "5432")
    monkeypatch.setenv(LYRA_POSTGRES_DB_ENV, " lyra ")
    monkeypatch.setenv(LYRA_POSTGRES_USER_ENV, " lyra ")
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, "  postgres-secret\n")
    monkeypatch.setenv(LYRA_ADMIN_API_KEY_ENV, "\nadmin-secret  ")
    monkeypatch.setenv(LYRA_AGENT_API_KEY_ENV, "\nagent-secret  ")


def test_config_contract_accepts_complete_schema(tmp_path: Path) -> None:
    config = LyraConfig.model_validate(_valid_config(tmp_path))

    assert config.schema_version == 1
    assert config.api.host == "0.0.0.0"
    assert config.api.port == 5219
    assert config.redis.url == "redis://redis:6379/0"
    assert config.database.host == "postgres"
    assert config.database.port == 5432
    assert config.database.name == "lyra"
    assert config.database.user == "lyra"
    assert config.database.read_password() == "postgres-secret"
    assert config.earth_engine.service_account_file == (
        tmp_path / "secrets" / "service-account.json"
    )
    assert config.admin.read_api_key() == "admin-secret"
    assert config.agent.read_api_key() == "agent-secret"
    assert config.mcp.enabled is False
    assert config.mcp.mount_path == DEFAULT_MCP_MOUNT_PATH
    assert config.logging.level == "INFO"
    assert config.logging.file == tmp_path / "logs" / "lyra.log"
    assert config.job_store.ttl_seconds == 600
    assert config.plugins.allowed_queues == ["interactive", "batch"]
    assert config.get_worker("interactive").concurrency == 32


def test_config_contract_applies_documented_field_defaults(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["api"] = {}
    del raw["earth_engine"]["service_account_file"]
    raw["logging"] = {}
    raw["job_store"] = {}
    del raw["plugins"]["catalog_dir"]
    del raw["plugins"]["runner_base_dir"]
    raw["workers"]["interactive"] = {"queues": ["interactive"]}

    config = LyraConfig.model_validate(raw)

    assert config.api.host == DEFAULT_API_HOST
    assert config.api.port == DEFAULT_API_PORT
    assert config.database.host == "postgres"
    assert config.database.port == 5432
    assert config.database.name == "lyra"
    assert config.database.user == "lyra"
    assert config.database.read_password() == "postgres-secret"
    assert config.earth_engine.service_account_file == (
        DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
    )
    assert config.admin.read_api_key() == "admin-secret"
    assert config.agent.read_api_key() == "agent-secret"
    assert config.mcp.enabled is False
    assert config.mcp.mount_path == DEFAULT_MCP_MOUNT_PATH
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


def test_config_contract_accepts_mcp_section_with_agent_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    raw["mcp"] = {
        "enabled": True,
        "mount_path": "/agent-mcp",
    }
    monkeypatch.setenv(LYRA_AGENT_API_KEY_ENV, " agent-secret ")

    config = LyraConfig.model_validate(raw)

    assert config.mcp.enabled is True
    assert config.mcp.mount_path == "/agent-mcp"
    assert config.agent.read_api_key() == "agent-secret"


def test_config_contract_rejects_job_access_without_agent_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    monkeypatch.delenv(LYRA_AGENT_API_KEY_ENV, raising=False)

    with pytest.raises(ConfigSecretError, match=LYRA_AGENT_API_KEY_ENV):
        LyraConfig.model_validate(raw)


def test_config_contract_rejects_mcp_secret_in_toml(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["mcp"] = {"api_key": "not-here"}

    _assert_invalid(raw, "Extra inputs are not permitted")


def test_config_contract_rejects_agent_secret_in_toml(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["agent"] = {"api_key": "not-here"}

    _assert_invalid(raw, r"\[agent\].*environment variables")


@pytest.mark.parametrize("mount_path", ["mcp", "/mcp/"])
def test_config_contract_rejects_invalid_mcp_mount_path(
    tmp_path: Path,
    mount_path: str,
) -> None:
    raw = _valid_config(tmp_path)
    raw["mcp"] = {"mount_path": mount_path}

    _assert_invalid(raw, "mcp.mount_path")


def test_config_contract_requires_known_schema_version(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    raw["schema_version"] = 2

    _assert_invalid(raw, "Input should be 1")


@pytest.mark.parametrize(
    ("section", "field", "value", "match"),
    [
        ("api", "port", 0, "greater than or equal to 1"),
        ("redis", "url", "postgres://db:5432/lyra", "redis.url"),
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
        ("earth_engine", "service_account_file"),
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


@pytest.mark.parametrize("section", ["admin", "database"])
def test_config_contract_rejects_env_backed_toml_sections(
    tmp_path: Path,
    section: str,
) -> None:
    raw = _valid_config(tmp_path)
    raw[section] = {}

    _assert_invalid(raw, "environment variables")


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
    assert config.agent.read_api_key() == "agent-secret"
    assert "agent-secret" not in repr(config)


def test_config_contract_reports_missing_admin_api_key_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    monkeypatch.delenv(LYRA_ADMIN_API_KEY_ENV)

    with pytest.raises(ConfigSecretError, match=LYRA_ADMIN_API_KEY_ENV):
        LyraConfig.model_validate(raw)


def test_config_contract_reports_missing_agent_api_key_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    monkeypatch.delenv(LYRA_AGENT_API_KEY_ENV)

    with pytest.raises(ConfigSecretError, match=LYRA_AGENT_API_KEY_ENV):
        LyraConfig.model_validate(raw)


def test_config_contract_reports_empty_postgres_password_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, " \n")

    with pytest.raises(ConfigSecretError, match=LYRA_POSTGRES_PASSWORD_ENV):
        LyraConfig.model_validate(raw)


def test_config_contract_reports_invalid_postgres_port_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _valid_config(tmp_path)
    monkeypatch.setenv(LYRA_POSTGRES_PORT_ENV, "70000")

    _assert_invalid(raw, "less than or equal to 65535")


def test_config_contract_rejects_empty_required_sections(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    del raw["redis"]

    _assert_invalid(raw, "Field required")


def test_config_contract_input_is_not_mutated(tmp_path: Path) -> None:
    raw = _valid_config(tmp_path)
    original = deepcopy(raw)

    LyraConfig.model_validate(raw)

    assert raw == original
