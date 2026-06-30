from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lyra_app.config import (
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_POSTGRES_DB_ENV,
    LYRA_POSTGRES_HOST_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LYRA_POSTGRES_PORT_ENV,
    LYRA_POSTGRES_USER_ENV,
    LyraConfig,
    clear_config_cache,
    get_config,
    save_config,
)
from lyra_app.plugin_state import (
    PluginState,
    PluginStateStore,
    make_repo_record,
    save_plugin_state,
)

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
            LYRA_POSTGRES_HOST_ENV: "postgres",
            LYRA_POSTGRES_PORT_ENV: "5432",
            LYRA_POSTGRES_DB_ENV: "lyra",
            LYRA_POSTGRES_USER_ENV: "lyra",
            LYRA_POSTGRES_PASSWORD_ENV: "postgres-secret",
            LYRA_ADMIN_API_KEY_ENV: "admin-secret",
        }
    )


def plugin_state_path(base: Path) -> Path:
    return base / "state" / "plugins.toml"


def plugin_state_store(base: Path, config: LyraConfig) -> PluginStateStore:
    return PluginStateStore(
        plugin_state_path(base),
        allowed_queues=config.plugins.allowed_queues,
    )


def load_test_config(
    base: Path,
    *,
    metric_queues: dict[str, str] | None = None,
    repos: list[str] | None = None,
) -> LyraConfig:
    secrets = _write_secret_files(base)
    _set_config_env()
    assigned_queues = set((metric_queues or {}).values())
    allowed_queues = sorted(
        {"batch", "heavy", "interactive", "lightweight", "priority-lane"}
        | assigned_queues
    )
    raw_config = {
        "schema_version": 1,
        "api": {},
        "redis": {"url": "redis://redis:6379/0"},
        "earth_engine": {
            "project": "earth-engine-project",
            "service_account_file": str(secrets["service_account"]),
        },
        "logging": {},
        "job_store": {},
        "plugins": {
            "catalog_dir": str(base / "plugins" / "catalog"),
            "runner_base_dir": str(base / "plugins" / "runners"),
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
    config = LyraConfig.model_validate(raw_config)
    config_path = base / "config" / "lyra.toml"
    save_config(config, config_path)
    save_plugin_state(
        PluginState(
            repos=[make_repo_record(repo) for repo in (repos or [])],
            metric_queues={} if metric_queues is None else metric_queues,
        ),
        plugin_state_path(base),
        allowed_queues=allowed_queues,
    )
    clear_config_cache()
    return get_config(config_path)
