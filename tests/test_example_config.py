import tomllib
from pathlib import Path

from lyra_app.config import (
    DEFAULT_ADMIN_API_KEY_FILE,
    DEFAULT_DATABASE_PASSWORD_FILE,
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    LyraConfig,
    render_config_toml,
)


def test_example_config_matches_config_contract() -> None:
    example_path = Path(__file__).resolve().parents[1] / "lyra.toml.example"
    raw_config = tomllib.loads(example_path.read_text(encoding="utf-8"))

    config = LyraConfig.model_validate(raw_config)

    assert "repos" not in raw_config["plugins"]
    assert "metric_queues" not in raw_config["plugins"]
    assert config.plugins.default_queue in config.plugins.allowed_queues
    assert config.database.password_file == DEFAULT_DATABASE_PASSWORD_FILE
    assert config.earth_engine.service_account_file == (
        DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE
    )
    assert config.admin.api_key_file == DEFAULT_ADMIN_API_KEY_FILE
    assert config.logging.file is None
    assert config.plugins.catalog_dir == DEFAULT_PLUGIN_CATALOG_DIR
    assert config.plugins.runner_base_dir == DEFAULT_PLUGIN_RUNNER_BASE_DIR
    assert set(config.workers) == {"batch", "interactive"}

    rendered = render_config_toml(config)

    assert "password_file" not in rendered
    assert "service_account_file" not in rendered
    assert "api_key_file" not in rendered
    assert "catalog_dir" not in rendered
    assert "runner_base_dir" not in rendered
    assert "repos" not in rendered
    assert "metric_queues" not in rendered
