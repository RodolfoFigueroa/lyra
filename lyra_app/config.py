"""Startup-only configuration and external credential resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lyra.sdk.config import (
    DEFAULT_AGENT_SUBMISSION_LIMIT,
    DEFAULT_AGENT_SUBMISSION_WINDOW_SECONDS,
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_CONFIG_PATH,
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_FORWARDED_ALLOW_IPS,
    DEFAULT_JOB_EVENT_MAX_EVENTS_PER_SECOND,
    DEFAULT_JOB_EVENT_MAX_PAYLOAD_BYTES,
    DEFAULT_JOB_EVENT_MAX_STREAM_EVENTS,
    DEFAULT_JOB_EVENT_PROGRESS_MIN_INTERVAL_MS,
    DEFAULT_JOB_STORE_TTL_SECONDS,
    DEFAULT_LOG_DIR,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MCP_MOUNT_PATH,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    DEFAULT_WORKER_CONCURRENCY,
    LYRA_DATA_DIR,
    AgentSubmissionLimitConfig,
    ApiConfig,
    DatabasePoolConfig,
    EarthEngineConfig,
    JobEventsConfig,
    JobStoreConfig,
    LoggingConfig,
    McpConfig,
    PluginRepoConfig,
    PluginsConfig,
    RedisConfig,
    StrictConfigModel,
    WorkerConfig,
)
from lyra.sdk.config import DatabaseConfig as ConfigDatabase
from lyra.sdk.config import LyraConfig as ConfigDocument
from lyra.sdk.config import load_config as load_document
from pydantic import Field, field_validator

LYRA_POSTGRES_PASSWORD_ENV = "LYRA_POSTGRES_PASSWORD"  # ruff:ignore[hardcoded-password-string]
LYRA_ADMIN_API_KEY_ENV = "LYRA_ADMIN_API_KEY"
LYRA_AGENT_API_KEY_ENV = "LYRA_AGENT_API_KEY"


class ConfigSecretError(RuntimeError):
    """Raised when a runtime secret cannot be resolved to a usable value."""


class ConfigLoadError(RuntimeError):
    """Raised when the TOML config file cannot be loaded or validated."""


def read_scalar_env_var(env_var: str, *, field_name: str) -> str:
    """Read a required, nonblank string value from an environment variable.

    Returns:
        The environment value with surrounding whitespace removed.

    Raises:
        ConfigSecretError: If the variable is unset or contains only whitespace.
    """
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        msg = f"{field_name} environment variable is not set: {env_var}"
        raise ConfigSecretError(msg)

    value = raw_value.strip()
    if not value:
        msg = f"{field_name} environment variable is empty: {env_var}"
        raise ConfigSecretError(msg)
    return value


def require_nonempty_file(path: Path, *, field_name: str) -> None:
    """Require a path to identify a readable file with non-whitespace content.

    Raises:
        ConfigSecretError: If the file cannot be read or has no non-whitespace
            bytes.
    """
    try:
        value = path.read_bytes()
    except OSError as exc:
        msg = f"{field_name} does not point to a readable file: {path}"
        raise ConfigSecretError(msg) from exc

    if not value.strip():
        msg = f"{field_name} file is empty: {path}"
        raise ConfigSecretError(msg)


class AdminConfig(StrictConfigModel):
    """Configure the environment-owned administrator credential."""

    api_key: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_ADMIN_API_KEY_ENV,
            field_name="admin.api_key",
        ),
        min_length=1,
        repr=False,
        description="Admin Bearer key supplied by LYRA_ADMIN_API_KEY.",
    )

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        """Strip and reject a blank administrator API key.

        Returns:
            The normalized, nonblank administrator key.

        Raises:
            ValueError: If the key is blank.
        """
        value = value.strip()
        if not value:
            msg = "API key must not be blank"
            raise ValueError(msg)
        return value

    def read_api_key(self) -> str:
        """Return the resolved administrator API key.

        Returns:
            The administrator key loaded from the process environment.
        """
        return self.api_key


class AgentConfig(StrictConfigModel):
    """Configure the environment-owned agent credential."""

    api_key: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_AGENT_API_KEY_ENV,
            field_name="agent.api_key",
        ),
        min_length=1,
        repr=False,
        description="Agent Bearer key supplied by LYRA_AGENT_API_KEY.",
    )

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        """Strip and reject a blank agent API key.

        Returns:
            The normalized, nonblank agent key.

        Raises:
            ValueError: If the key is blank.
        """
        value = value.strip()
        if not value:
            msg = "API key must not be blank"
            raise ValueError(msg)
        return value

    def read_api_key(self) -> str:
        """Return the resolved agent API key.

        Returns:
            The agent key loaded from the process environment.
        """
        return self.api_key


class DatabaseConfig(ConfigDatabase):
    """Database configuration with its password resolved at process startup."""

    password: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_POSTGRES_PASSWORD_ENV, field_name="database.password"
        ),
        repr=False,
        exclude=True,
        description="Database password supplied by LYRA_POSTGRES_PASSWORD.",
    )

    def read_password(self) -> str:
        """Return the startup password.

        Returns:
            The resolved database password.
        """
        return self.password


class LyraConfig(ConfigDocument):
    """Loaded configuration and credentials captured for this process."""

    database: DatabaseConfig
    admin: AdminConfig = Field(default_factory=AdminConfig, exclude=True)
    agent: AgentConfig = Field(default_factory=AgentConfig, exclude=True)


@dataclass
class _ConfigCache:
    config: LyraConfig | None = None
    path: Path | None = None


_cache = _ConfigCache()


def initialize_runtime_config(config: LyraConfig) -> None:
    """Bind the startup configuration used by all consumers in this process."""
    _cache.config = config
    _cache.path = _cache.path or DEFAULT_CONFIG_PATH


def parse_config_toml(raw_config: dict[str, object]) -> LyraConfig:
    """Validate a document and resolve process credentials.

    Returns:
        The runtime configuration.
    """
    return LyraConfig.model_validate(
        ConfigDocument.model_validate(raw_config).model_dump()
    )


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LyraConfig:
    """Load configuration and validate external credentials.

    Returns:
        The process configuration.

    Raises:
        ConfigLoadError: If configuration or credentials cannot be loaded.
    """
    try:
        config = LyraConfig.model_validate(load_document(path).model_dump())
        validate_config_secret_references(config)
    except (OSError, ValueError, ConfigSecretError) as exc:
        msg = f"Cannot load configuration {path}: {exc}"
        raise ConfigLoadError(msg) from exc
    return config


def validate_config_secret_references(config: LyraConfig) -> None:
    """Resolve and validate all runtime-owned secrets referenced by a config."""
    config.database.read_password()
    config.admin.read_api_key()
    config.agent.read_api_key()
    require_nonempty_file(
        config.earth_engine.service_account_file,
        field_name="earth_engine.service_account_file",
    )


def ensure_runtime_directories(config: LyraConfig) -> None:
    """Create non-secret runtime directories declared by the server config."""
    paths = {config.plugins.catalog_dir, config.plugins.runner_base_dir}
    if config.logging.file is not None:
        paths.add(config.logging.file.parent)

    for worker_name in config.workers:
        paths.add(config.worker_install_dir(worker_name))
        paths.add(config.worker_temp_dir(worker_name))

    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def get_config(path: str | Path | None = None) -> LyraConfig:
    """Return the cached runtime config, loading it when necessary.

    Returns:
        The cached configuration for ``path`` or the default configuration path.
    """
    config_path = Path(path) if path is not None else _cache.path
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if _cache.config is None or config_path != _cache.path:
        _cache.config = load_config(config_path)
        _cache.path = config_path
    return _cache.config


def get_config_path() -> Path:
    """Return the path associated with the cached runtime configuration.

    Returns:
        The cached source path, or the default path before a config is loaded.
    """
    return _cache.path or DEFAULT_CONFIG_PATH


def clear_config_cache() -> None:
    """Discard the cached configuration and its source path."""
    _cache.config = None
    _cache.path = None


__all__ = [
    "DEFAULT_AGENT_SUBMISSION_LIMIT",
    "DEFAULT_AGENT_SUBMISSION_WINDOW_SECONDS",
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE",
    "DEFAULT_FORWARDED_ALLOW_IPS",
    "DEFAULT_JOB_EVENT_MAX_EVENTS_PER_SECOND",
    "DEFAULT_JOB_EVENT_MAX_PAYLOAD_BYTES",
    "DEFAULT_JOB_EVENT_MAX_STREAM_EVENTS",
    "DEFAULT_JOB_EVENT_PROGRESS_MIN_INTERVAL_MS",
    "DEFAULT_JOB_STORE_TTL_SECONDS",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_MCP_MOUNT_PATH",
    "DEFAULT_PLUGIN_CATALOG_DIR",
    "DEFAULT_PLUGIN_RUNNER_BASE_DIR",
    "DEFAULT_WORKER_CONCURRENCY",
    "LYRA_DATA_DIR",
    "AdminConfig",
    "AgentConfig",
    "AgentSubmissionLimitConfig",
    "ApiConfig",
    "ConfigLoadError",
    "ConfigSecretError",
    "DatabaseConfig",
    "DatabasePoolConfig",
    "EarthEngineConfig",
    "JobEventsConfig",
    "JobStoreConfig",
    "LoggingConfig",
    "LyraConfig",
    "McpConfig",
    "PluginRepoConfig",
    "PluginsConfig",
    "RedisConfig",
    "StrictConfigModel",
    "WorkerConfig",
    "clear_config_cache",
    "ensure_runtime_directories",
    "get_config",
    "get_config_path",
    "load_config",
    "parse_config_toml",
    "read_scalar_env_var",
    "require_nonempty_file",
    "validate_config_secret_references",
]
