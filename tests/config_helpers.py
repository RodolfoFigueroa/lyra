from __future__ import annotations

import os
from typing import TYPE_CHECKING

from lyra.sdk.config import PluginRepoConfig

from lyra_app.config import (
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LyraConfig,
    clear_config_cache,
    get_config,
)
from lyra_app.plugins import parse_repo_entry
from tests.config_serialization import save_config

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


DEFAULT_TEST_PLUGIN_REPO = "owner/repo"


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
        "schema_version": 2,
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
    declarations = list(repos) if repos is not None else []
    if metric_queues and not declarations:
        declarations = [DEFAULT_TEST_PLUGIN_REPO]
    records = []
    for source in declarations:
        entry = parse_repo_entry(source)
        canonical = (
            entry.clone_url
            if entry.source_kind == "directory"
            else entry.source_path.as_uri()
            if entry.source_path is not None
            else f"{entry.owner}/{entry.repo}"
        )
        records.append(
            PluginRepoConfig(
                id=entry.target_name,
                source=canonical,
                ref=entry.ref,
                routing=metric_queues or {} if not records else {},
            )
        )
    config = LyraConfig.model_validate(raw_config)
    config.plugins.repos = records
    config_path = base / "config" / "lyra.toml"
    save_config(config, config_path)
    clear_config_cache()
    return get_config(config_path)
