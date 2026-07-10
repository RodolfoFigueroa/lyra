import tomllib
from pathlib import Path

import pytest

from lyra_app.config import (
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_POSTGRES_DB_ENV,
    LYRA_POSTGRES_HOST_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LYRA_POSTGRES_PORT_ENV,
    LYRA_POSTGRES_USER_ENV,
    LyraConfig,
    render_config_toml,
)


def test_example_config_matches_config_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LYRA_POSTGRES_HOST_ENV, "postgres")
    monkeypatch.setenv(LYRA_POSTGRES_PORT_ENV, "5432")
    monkeypatch.setenv(LYRA_POSTGRES_DB_ENV, "lyra")
    monkeypatch.setenv(LYRA_POSTGRES_USER_ENV, "lyra")
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, "postgres-secret")
    monkeypatch.setenv(LYRA_ADMIN_API_KEY_ENV, "admin-secret")
    monkeypatch.setenv(LYRA_AGENT_API_KEY_ENV, "agent-secret")
    example_path = Path(__file__).resolve().parents[1] / "config.example.toml"
    raw_config = tomllib.loads(example_path.read_text(encoding="utf-8"))

    config = LyraConfig.model_validate(raw_config)

    assert "repos" not in raw_config["plugins"]
    assert "metric_queues" not in raw_config["plugins"]
    assert config.plugins.default_queue in config.plugins.allowed_queues
    assert "database" not in raw_config
    assert "admin" not in raw_config
    assert "agent" not in raw_config
    assert config.database.read_password() == "postgres-secret"
    assert config.admin.read_api_key() == "admin-secret"
    assert config.agent.read_api_key() == "agent-secret"
    assert config.earth_engine.service_account_file == (
        DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
    )
    assert config.logging.file is None
    assert config.plugins.catalog_dir == DEFAULT_PLUGIN_CATALOG_DIR
    assert config.plugins.runner_base_dir == DEFAULT_PLUGIN_RUNNER_BASE_DIR
    assert set(config.workers) == {"batch", "interactive"}

    rendered = render_config_toml(config)

    assert "[database]" not in rendered
    assert "[admin]" not in rendered
    assert "[agent]" not in rendered
    assert "password_file" not in rendered
    assert "service_account_file" not in rendered
    assert "api_key_file" not in rendered
    assert "catalog_dir" not in rendered
    assert "runner_base_dir" not in rendered
    assert "repos" not in rendered
    assert "metric_queues" not in rendered
