import tomllib
from pathlib import Path

import pytest

from lyra_app.config import (
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LyraConfig,
)
from tests.config_serialization import render_config_toml


def test_example_config_matches_config_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, "postgres-secret")
    monkeypatch.setenv(LYRA_ADMIN_API_KEY_ENV, "admin-secret")
    monkeypatch.setenv(LYRA_AGENT_API_KEY_ENV, "agent-secret")
    example_path = Path(__file__).resolve().parents[1] / "config.example.toml"
    raw_config = tomllib.loads(example_path.read_text(encoding="utf-8"))

    config = LyraConfig.model_validate(raw_config)

    assert "repos" not in raw_config["plugins"]
    assert "metric_queues" not in raw_config["plugins"]
    assert config.plugins.default_queue in config.plugins.allowed_queues
    assert "password" not in raw_config["database"]
    assert "host" in raw_config["database"]
    assert config.database.api.pool_size == 5
    assert "admin" not in raw_config
    assert "agent" not in raw_config
    assert config.database.read_password() == "postgres-secret"
    assert config.admin.read_api_key() == "admin-secret"
    assert config.agent.read_api_key() == "agent-secret"
    assert config.api.forwarded_allow_ips == ["127.0.0.1"]
    assert config.earth_engine.service_account_file == (
        DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
    )
    assert config.logging.file is None
    assert set(config.workers) == {"batch", "interactive"}

    rendered = render_config_toml(config)

    assert "[database]" in rendered
    assert "[database.api]" in rendered
    assert "[database.spatial]" in rendered
    assert "[database.worker]" in rendered
    rendered_config = tomllib.loads(rendered)
    assert "host" in rendered_config["database"]
    assert "password" not in rendered_config["database"]
    assert "[admin]" not in rendered
    assert "[agent]" not in rendered
    assert "password_file" not in rendered
    assert "service_account_file" not in rendered
    assert "api_key_file" not in rendered
    assert "catalog_dir" not in rendered
    assert "runner_base_dir" not in rendered
    assert "repos" not in rendered
    assert "metric_queues" not in rendered
    assert 'forwarded_allow_ips = [\n  "127.0.0.1",\n]' in rendered
