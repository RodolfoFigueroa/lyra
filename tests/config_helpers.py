from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lyra.sdk.config import InstalledPluginConfig

from lyra_app.config import (
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LyraConfig,
    clear_config_cache,
    get_config,
)
from tests.config_serialization import save_config
from tests.plugin_helpers import plugin_config

if TYPE_CHECKING:
    from pathlib import Path


def _write_secret_files(base: Path) -> dict[str, Path]:
    secrets_dir = base / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "service_account": secrets_dir / "service-account.json",
    }
    paths["service_account"].write_text(
        '{"client_email":"test@example.com"}',
        encoding="utf-8",
    )
    return paths


def _set_config_env() -> None:
    os.environ.update(
        {
            LYRA_POSTGRES_PASSWORD_ENV: "postgres-secret",
            LYRA_ADMIN_API_KEY_ENV: "admin-secret",
            LYRA_AGENT_API_KEY_ENV: "agent-secret",
        }
    )


DEFAULT_TEST_PLUGIN = "test-plugin"


def load_test_config(
    base: Path,
    *,
    metric_queues: dict[str, str] | None = None,
    plugins: list[Path] | None = None,
) -> LyraConfig:
    secrets = _write_secret_files(base)
    _set_config_env()
    assigned_queues = set((metric_queues or {}).values())
    allowed_queues = sorted(
        {"batch", "heavy", "interactive", "lightweight", "priority-lane"}
        | assigned_queues
    )
    raw_config = {
        "schema_version": 3,
        "api": {"public_base_url": "http://127.0.0.1:5219"},
        "redis": {"url": "redis://redis:6379/0"},
        "database": {"host": "postgres", "port": 5432, "name": "lyra", "user": "lyra"},
        "earth_engine": {
            "project": "earth-engine-project",
            "service_account_file": str(secrets["service_account"]),
        },
        "logging": {},
        "job_store": {},
        "agent_submission_limit": {},
        "plugins": {
            "default_queue": "interactive",
            "allowed_queues": allowed_queues,
        },
        "workers": {
            "batch": {"queues": ["batch"]},
            "heavy": {"queues": ["heavy"]},
            "interactive": {"queues": ["interactive"]},
            "lightweight": {"queues": ["lightweight"]},
            "priority": {"queues": ["priority-lane"]},
        },
    }
    records = [plugin_config(path, routing=metric_queues) for path in (plugins or [])]
    if metric_queues and not records:
        records = [
            InstalledPluginConfig(
                distribution=DEFAULT_TEST_PLUGIN, routing=metric_queues
            )
        ]
    config = LyraConfig.model_validate(raw_config)
    config.plugins.installed = records
    config_path = base / "config" / "lyra.toml"
    save_config(config, config_path)
    clear_config_cache()
    return get_config(config_path)
