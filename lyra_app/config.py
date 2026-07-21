from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from tomllib import TOMLDecodeError
from typing import Literal, Self
from urllib.parse import urlparse, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from lyra_app.toml import (
    TomlNormalizationError,
    TomlTable,
    load_normalized_toml,
    normalize_toml_table,
)

LYRA_DATA_DIR = Path("/lyra_data")
DEFAULT_CONFIG_PATH = LYRA_DATA_DIR / "config" / "lyra.toml"
DEFAULT_API_HOST = str(ipaddress.IPv4Address(0))
DEFAULT_API_PORT = 5219
DEFAULT_FORWARDED_ALLOW_IPS = ["127.0.0.1"]
DEFAULT_JOB_STORE_TTL_SECONDS = 600
DEFAULT_AGENT_SUBMISSION_LIMIT = 10
DEFAULT_AGENT_SUBMISSION_WINDOW_SECONDS = 60
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_CONCURRENCY = 1
DEFAULT_LOG_DIR = LYRA_DATA_DIR / "logs"
DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE = (
    LYRA_DATA_DIR / "secrets" / "service-account.json"
)
DEFAULT_PLUGIN_CATALOG_DIR = LYRA_DATA_DIR / "plugins" / "catalog"
DEFAULT_PLUGIN_RUNNER_BASE_DIR = LYRA_DATA_DIR / "plugins" / "runners"
LYRA_POSTGRES_HOST_ENV = "LYRA_POSTGRES_HOST"
LYRA_POSTGRES_PORT_ENV = "LYRA_POSTGRES_PORT"
LYRA_POSTGRES_DB_ENV = "LYRA_POSTGRES_DB"
LYRA_POSTGRES_USER_ENV = "LYRA_POSTGRES_USER"
LYRA_POSTGRES_PASSWORD_ENV = "LYRA_POSTGRES_PASSWORD"  # noqa: S105
LYRA_ADMIN_API_KEY_ENV = "LYRA_ADMIN_API_KEY"
LYRA_AGENT_API_KEY_ENV = "LYRA_AGENT_API_KEY"
DEFAULT_MCP_MOUNT_PATH = "/mcp"

_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_ALLOWED_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())
_BARE_TOML_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ENV_BACKED_CONFIG_SECTIONS = frozenset({"admin", "agent"})
_DATABASE_ENV_BACKED_FIELDS = frozenset({"host", "port", "name", "user", "password"})


class ConfigSecretError(RuntimeError):
    """Raised when a runtime secret cannot be resolved to a usable value."""


class ConfigLoadError(RuntimeError):
    """Raised when the TOML config file cannot be loaded or validated."""


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


_CONFIG_CACHE: LyraConfig | None = None
_CONFIG_CACHE_PATH: Path | None = None


def _strip_required_string(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        msg = "value must be a non-empty string"
        raise ValueError(msg)
    return stripped


def _validate_absolute_path(path: Path | None) -> Path | None:
    if path is not None and not path.is_absolute():
        msg = "path must be absolute"
        raise ValueError(msg)
    return path


def _strip_string_list(value: list[str]) -> list[str]:
    return [_strip_required_string(item) for item in value]


def read_scalar_env_var(env_var: str, *, field_name: str) -> str:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        msg = f"{field_name} environment variable is not set: {env_var}"
        raise ConfigSecretError(msg)

    value = raw_value.strip()
    if not value:
        msg = f"{field_name} environment variable is empty: {env_var}"
        raise ConfigSecretError(msg)
    return value


def read_int_env_var(env_var: str, *, field_name: str) -> int:
    raw_value = read_scalar_env_var(env_var, field_name=field_name)
    try:
        return int(raw_value)
    except ValueError as exc:
        msg = f"{field_name} environment variable must be an integer: {env_var}"
        raise ConfigSecretError(msg) from exc


def read_scalar_secret_file(path: Path, *, field_name: str) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        msg = f"{field_name} does not point to a readable secret file: {path}"
        raise ConfigSecretError(msg) from exc

    if not value:
        msg = f"{field_name} secret file is empty: {path}"
        raise ConfigSecretError(msg)
    return value


def require_nonempty_file(path: Path, *, field_name: str) -> None:
    try:
        value = path.read_bytes()
    except OSError as exc:
        msg = f"{field_name} does not point to a readable file: {path}"
        raise ConfigSecretError(msg) from exc

    if not value.strip():
        msg = f"{field_name} file is empty: {path}"
        raise ConfigSecretError(msg)


class ApiConfig(StrictConfigModel):
    host: str = Field(
        default=DEFAULT_API_HOST,
        description="Interface address on which the API server listens.",
    )
    port: int = Field(
        default=DEFAULT_API_PORT,
        ge=1,
        le=65535,
        description="TCP port on which the API server listens.",
    )
    public_base_url: str = Field(
        min_length=1,
        description="Externally reachable base URL used in result handoffs.",
    )
    forwarded_allow_ips: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORWARDED_ALLOW_IPS),
        description="Proxy IP addresses or CIDRs trusted to set forwarded headers.",
    )

    @field_validator("host", "public_base_url")
    @classmethod
    def normalize_required_strings(cls, value: str) -> str:
        return _strip_required_string(value)

    @field_validator("forwarded_allow_ips")
    @classmethod
    def normalize_forwarded_allow_ips(cls, value: list[str]) -> list[str]:
        return _strip_string_list(value)

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            msg = "api.public_base_url must be an absolute http:// or https:// URL"
            raise ValueError(msg)
        if parsed.username is not None or parsed.password is not None:
            msg = "api.public_base_url must not contain credentials"
            raise ValueError(msg)
        if parsed.query or parsed.fragment:
            msg = "api.public_base_url must not contain a query or fragment"
            raise ValueError(msg)

        hostname = parsed.hostname
        if hostname is None:
            msg = "api.public_base_url must contain a hostname"
            raise ValueError(msg)
        try:
            _port = parsed.port
        except ValueError as exc:
            msg = "api.public_base_url contains an invalid port"
            raise ValueError(msg) from exc

        is_loopback = hostname.lower() == "localhost"
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            is_loopback = address.is_loopback
        elif "." not in hostname and not is_loopback:
            msg = (
                "api.public_base_url must use a public hostname, not a "
                "single-label internal hostname"
            )
            raise ValueError(msg)

        if parsed.scheme == "http" and not is_loopback:
            msg = (
                "api.public_base_url must use https; http is allowed only for "
                "loopback development"
            )
            raise ValueError(msg)

        return value.rstrip("/")


class RedisConfig(StrictConfigModel):
    url: str = Field(
        min_length=1,
        description="Redis URL used by Celery and the retained job store.",
    )

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return _strip_required_string(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in _ALLOWED_REDIS_SCHEMES or not parsed.netloc:
            msg = "redis.url must be a redis:// or rediss:// URL"
            raise ValueError(msg)
        return value


class DatabasePoolConfig(StrictConfigModel):
    pool_size: int = Field(ge=1, description="Persistent connections per process.")
    max_overflow: int = Field(
        default=0,
        ge=0,
        description="Temporary connections allowed above the pool size.",
    )
    pool_timeout_seconds: float = Field(
        gt=0,
        description="Maximum wait for a pooled connection.",
    )
    connect_timeout_seconds: int = Field(
        gt=0,
        description="Maximum time allowed to establish a database connection.",
    )
    statement_timeout_ms: int = Field(
        gt=0,
        description="PostgreSQL statement timeout for this workload.",
    )
    pool_recycle_seconds: int = Field(
        gt=0,
        description="Age after which pooled connections are replaced.",
    )


def _api_database_pool() -> DatabasePoolConfig:
    return DatabasePoolConfig(
        pool_size=5,
        pool_timeout_seconds=2.0,
        connect_timeout_seconds=5,
        statement_timeout_ms=10_000,
        pool_recycle_seconds=900,
    )


def _spatial_database_pool() -> DatabasePoolConfig:
    return DatabasePoolConfig(
        pool_size=2,
        pool_timeout_seconds=2.0,
        connect_timeout_seconds=5,
        statement_timeout_ms=25_000,
        pool_recycle_seconds=900,
    )


def _worker_database_pool() -> DatabasePoolConfig:
    return DatabasePoolConfig(
        pool_size=1,
        pool_timeout_seconds=5.0,
        connect_timeout_seconds=5,
        statement_timeout_ms=300_000,
        pool_recycle_seconds=900,
    )


class DatabaseConfig(StrictConfigModel):
    host: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_POSTGRES_HOST_ENV,
            field_name="database.host",
        ),
        min_length=1,
        description="PostgreSQL host supplied by LYRA_POSTGRES_HOST.",
    )
    port: int = Field(
        default_factory=lambda: read_int_env_var(
            LYRA_POSTGRES_PORT_ENV,
            field_name="database.port",
        ),
        ge=1,
        le=65535,
        description="PostgreSQL port supplied by LYRA_POSTGRES_PORT.",
    )
    name: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_POSTGRES_DB_ENV,
            field_name="database.name",
        ),
        min_length=1,
        description="PostgreSQL database supplied by LYRA_POSTGRES_DB.",
    )
    user: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_POSTGRES_USER_ENV,
            field_name="database.user",
        ),
        min_length=1,
        description="PostgreSQL user supplied by LYRA_POSTGRES_USER.",
    )
    password: str = Field(
        default_factory=lambda: read_scalar_env_var(
            LYRA_POSTGRES_PASSWORD_ENV,
            field_name="database.password",
        ),
        min_length=1,
        repr=False,
        description="PostgreSQL password supplied by LYRA_POSTGRES_PASSWORD.",
    )
    readiness_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        description="Timeout for each database readiness probe.",
    )
    retry_after_seconds: int = Field(
        default=5,
        gt=0,
        description="Retry delay advertised for temporary database failures.",
    )
    api: DatabasePoolConfig = Field(
        default_factory=_api_database_pool,
        description="Pool settings for ordinary asynchronous API queries.",
    )
    spatial: DatabasePoolConfig = Field(
        default_factory=_spatial_database_pool,
        description="Pool settings for API spatial-resolution queries.",
    )
    worker: DatabasePoolConfig = Field(
        default_factory=_worker_database_pool,
        description="Pool settings created inside each worker process.",
    )

    @field_validator("host", "name", "user", "password")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        return _strip_required_string(value)

    def read_password(self) -> str:
        return self.password


class EarthEngineConfig(StrictConfigModel):
    project: str = Field(
        min_length=1,
        description="Google Earth Engine project identifier.",
    )
    service_account_file: Path = Field(
        default=DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
        description="Absolute path to the Earth Engine service-account JSON.",
    )

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        return _strip_required_string(value)

    @field_validator("service_account_file")
    @classmethod
    def validate_service_account_file(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "earth_engine.service_account_file is required"
            raise ValueError(msg)
        return path


class AdminConfig(StrictConfigModel):
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
        return _strip_required_string(value)

    def read_api_key(self) -> str:
        return self.api_key


class AgentConfig(StrictConfigModel):
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
        return _strip_required_string(value)

    def read_api_key(self) -> str:
        return self.api_key


class McpConfig(StrictConfigModel):
    enabled: bool = Field(
        default=False,
        description="Whether to mount the Streamable HTTP MCP server.",
    )
    mount_path: str = Field(
        default=DEFAULT_MCP_MOUNT_PATH,
        description="Absolute URL path at which the MCP server is mounted.",
    )

    @field_validator("mount_path")
    @classmethod
    def normalize_mount_path(cls, value: str) -> str:
        return _strip_required_string(value)

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        if not value.startswith("/"):
            msg = "mcp.mount_path must start with /"
            raise ValueError(msg)
        if len(value) > 1 and value.endswith("/"):
            msg = "mcp.mount_path must not end with /"
            raise ValueError(msg)
        return value


class LoggingConfig(StrictConfigModel):
    level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description="Application logging level.",
    )
    file: Path | None = Field(
        default=None,
        description="Optional absolute log file; omit to log to standard output.",
    )

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        value = _strip_required_string(value)
        return value.upper()

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        if value not in _ALLOWED_LOG_LEVELS:
            levels = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            msg = f"logging.level must be one of: {levels}"
            raise ValueError(msg)
        return value

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: Path | None) -> Path | None:
        return _validate_absolute_path(value)


class JobStoreConfig(StrictConfigModel):
    ttl_seconds: int = Field(
        default=DEFAULT_JOB_STORE_TTL_SECONDS,
        gt=0,
        description="Retention time for job state, events, results, and idempotency.",
    )


class AgentSubmissionLimitConfig(StrictConfigModel):
    limit: int = Field(
        default=DEFAULT_AGENT_SUBMISSION_LIMIT,
        gt=0,
        strict=True,
        description="New REST and MCP submissions allowed in one fixed window.",
    )
    window_seconds: int = Field(
        default=DEFAULT_AGENT_SUBMISSION_WINDOW_SECONDS,
        gt=0,
        strict=True,
        description="Length of the shared submission-limit window.",
    )


class PluginsConfig(StrictConfigModel):
    catalog_dir: Path = Field(
        default=DEFAULT_PLUGIN_CATALOG_DIR,
        description="Absolute directory for API-side plugin catalog snapshots.",
    )
    runner_base_dir: Path = Field(
        default=DEFAULT_PLUGIN_RUNNER_BASE_DIR,
        description="Absolute parent directory for worker plugin installs.",
    )
    default_queue: str = Field(
        min_length=1,
        description="Queue assigned to newly discovered metrics.",
    )
    allowed_queues: list[str] = Field(
        min_length=1,
        description="Complete set of queues permitted in metric routing.",
    )
    initial_repos: list[str] = Field(
        default_factory=list,
        description="Plugin sources seeded only when plugin state is absent.",
    )

    @field_validator("allowed_queues", "initial_repos")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        return _strip_string_list(value)

    @field_validator("initial_repos")
    @classmethod
    def validate_initial_repos(cls, value: list[str]) -> list[str]:
        from lyra_app.plugin_state import (  # noqa: PLC0415
            PluginState,
            make_repo_record,
        )

        PluginState(repos=[make_repo_record(source) for source in value])
        return value

    @field_validator("catalog_dir", "runner_base_dir")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "plugin path fields are required"
            raise ValueError(msg)
        return path

    @field_validator("default_queue")
    @classmethod
    def normalize_default_queue(cls, value: str) -> str:
        return _strip_required_string(value)

    @model_validator(mode="after")
    def validate_queues(self) -> Self:
        allowed_queues = set(self.allowed_queues)
        if self.default_queue not in allowed_queues:
            msg = "plugins.default_queue must appear in plugins.allowed_queues"
            raise ValueError(msg)

        return self


class WorkerConfig(StrictConfigModel):
    queues: list[str] = Field(
        min_length=1,
        description="Queues imported and consumed by this worker pool.",
    )
    concurrency: int = Field(
        default=DEFAULT_WORKER_CONCURRENCY,
        gt=0,
        description="Celery child processes in this worker pool.",
    )
    install_dir: Path | None = Field(
        default=None,
        description="Optional absolute plugin install directory for this worker.",
    )
    temp_dir: Path | None = Field(
        default=None,
        description="Optional absolute per-job temporary-file parent directory.",
    )

    @field_validator("queues")
    @classmethod
    def normalize_queues(cls, value: list[str]) -> list[str]:
        return _strip_string_list(value)

    @field_validator("install_dir", "temp_dir")
    @classmethod
    def validate_paths(cls, value: Path | None) -> Path | None:
        return _validate_absolute_path(value)


class LyraConfig(StrictConfigModel):
    schema_version: Literal[1] = Field(
        description="Server configuration schema version."
    )
    api: ApiConfig = Field(description="API bind and public URL settings.")
    redis: RedisConfig = Field(description="Redis connection settings.")
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="PostgreSQL connection and pool settings.",
    )
    earth_engine: EarthEngineConfig = Field(
        description="Google Earth Engine credentials and project settings."
    )
    admin: AdminConfig = Field(
        default_factory=AdminConfig,
        description="Environment-owned administrator credential.",
    )
    agent: AgentConfig = Field(
        default_factory=AgentConfig,
        description="Environment-owned agent credential.",
    )
    mcp: McpConfig = Field(
        default_factory=McpConfig,
        description="MCP transport settings.",
    )
    logging: LoggingConfig = Field(description="Application logging settings.")
    job_store: JobStoreConfig = Field(description="Retained job-store settings.")
    agent_submission_limit: AgentSubmissionLimitConfig = Field(
        default_factory=AgentSubmissionLimitConfig,
        description="Shared REST and MCP submission limit.",
    )
    plugins: PluginsConfig = Field(description="Plugin source and routing defaults.")
    workers: dict[str, WorkerConfig] = Field(
        min_length=1,
        description="Named worker pool definitions.",
    )

    @model_validator(mode="after")
    def validate_worker_queues(self) -> Self:
        allowed_queues = set(self.plugins.allowed_queues)
        invalid: dict[str, list[str]] = {}
        for worker_name, worker in self.workers.items():
            invalid_queues = sorted(
                {queue for queue in worker.queues if queue not in allowed_queues}
            )
            if invalid_queues:
                invalid[worker_name] = invalid_queues

        if invalid:
            details = "; ".join(
                f"{worker}: {', '.join(queues)}"
                for worker, queues in sorted(invalid.items())
            )
            msg = (
                "workers.<name>.queues values must appear in "
                f"plugins.allowed_queues: {details}"
            )
            raise ValueError(msg)
        return self

    def get_worker(self, name: str) -> WorkerConfig:
        worker_name = _strip_required_string(name)
        try:
            return self.workers[worker_name]
        except KeyError as exc:
            msg = f"unknown worker config: {worker_name}"
            raise KeyError(msg) from exc

    def worker_install_dir(self, name: str) -> Path:
        worker_name = _strip_required_string(name)
        worker = self.get_worker(worker_name)
        return worker.install_dir or self.plugins.runner_base_dir / worker_name

    def worker_temp_dir(self, name: str) -> Path:
        worker_name = _strip_required_string(name)
        worker = self.get_worker(worker_name)
        return worker.temp_dir or LYRA_DATA_DIR / "cache" / "jobs" / worker_name


def validate_config_secret_references(config: LyraConfig) -> None:
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


def _reject_env_backed_config(raw_config: TomlTable) -> None:
    configured_sections = sorted(set(raw_config) & _ENV_BACKED_CONFIG_SECTIONS)
    if configured_sections:
        sections = ", ".join(f"[{section}]" for section in configured_sections)
        msg = (
            f"{sections} settings are configured through environment variables, "
            "not lyra.toml"
        )
        raise ValueError(msg)

    database = raw_config.get("database")
    if not isinstance(database, dict):
        return
    configured_fields = sorted(set(database) & _DATABASE_ENV_BACKED_FIELDS)
    if configured_fields:
        fields = ", ".join(f"database.{field}" for field in configured_fields)
        msg = f"{fields} are configured through environment variables"
        raise ValueError(msg)


def parse_config_toml(raw_config: TomlTable) -> LyraConfig:
    """Normalize and validate one TOML document as the runtime configuration."""

    raw_config = normalize_toml_table(raw_config)
    _reject_env_backed_config(raw_config)
    return LyraConfig.model_validate(raw_config)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LyraConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as config_file:
            raw_config = load_normalized_toml(config_file)
    except FileNotFoundError as exc:
        msg = f"Lyra config file does not exist: {config_path}"
        raise ConfigLoadError(msg) from exc
    except TOMLDecodeError as exc:
        msg = f"Lyra config file is not valid TOML: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc
    except TomlNormalizationError as exc:
        msg = f"Lyra config file failed normalization: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc
    except OSError as exc:
        msg = f"Lyra config file could not be read: {config_path}"
        raise ConfigLoadError(msg) from exc

    try:
        config = parse_config_toml(raw_config)
        validate_config_secret_references(config)
    except ValueError as exc:
        msg = f"Lyra config file failed validation: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc
    except ConfigSecretError as exc:
        msg = f"Lyra config file failed runtime secret validation: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc

    return config


def get_config(path: str | Path | None = None) -> LyraConfig:
    global _CONFIG_CACHE, _CONFIG_CACHE_PATH  # noqa: PLW0603

    config_path = Path(path) if path is not None else _CONFIG_CACHE_PATH
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH
    if _CONFIG_CACHE is None or config_path != _CONFIG_CACHE_PATH:
        _CONFIG_CACHE = load_config(config_path)
        _CONFIG_CACHE_PATH = config_path
    return _CONFIG_CACHE


def get_config_path() -> Path:
    return _CONFIG_CACHE_PATH or DEFAULT_CONFIG_PATH


def reload_config(path: str | Path | None = None) -> LyraConfig:
    global _CONFIG_CACHE, _CONFIG_CACHE_PATH  # noqa: PLW0603

    config_path = Path(path) if path is not None else _CONFIG_CACHE_PATH
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    _CONFIG_CACHE = load_config(config_path)
    _CONFIG_CACHE_PATH = config_path
    return _CONFIG_CACHE


def clear_config_cache() -> None:
    global _CONFIG_CACHE, _CONFIG_CACHE_PATH  # noqa: PLW0603

    _CONFIG_CACHE = None
    _CONFIG_CACHE_PATH = None


def _toml_string(value: str | Path) -> str:
    return json.dumps(str(value))


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY_PATTERN.fullmatch(value) else _toml_string(value)


def _toml_string_array(values: list[str]) -> str:
    if not values:
        return "[]"
    rendered_values = "\n".join(f"  {_toml_string(value)}," for value in values)
    return f"[\n{rendered_values}\n]"


def _append_key(
    lines: list[str],
    key: str,
    value: str | Path | float | list[str],
) -> None:
    if isinstance(value, int | float):
        rendered = str(value)
    elif isinstance(value, list):
        rendered = _toml_string_array(value)
    else:
        rendered = _toml_string(value)
    lines.append(f"{key} = {rendered}")


def _append_api_section(lines: list[str], api: ApiConfig) -> None:
    lines.append("[api]")
    _append_key(lines, "host", api.host)
    _append_key(lines, "port", api.port)
    _append_key(lines, "public_base_url", api.public_base_url)
    _append_key(lines, "forwarded_allow_ips", api.forwarded_allow_ips)
    lines.append("")


def _append_redis_section(lines: list[str], redis: RedisConfig) -> None:
    lines.append("[redis]")
    _append_key(lines, "url", redis.url)
    lines.append("")


def _append_database_pool_section(
    lines: list[str],
    name: str,
    pool: DatabasePoolConfig,
) -> None:
    lines.append(f"[database.{name}]")
    _append_key(lines, "pool_size", pool.pool_size)
    _append_key(lines, "max_overflow", pool.max_overflow)
    _append_key(lines, "pool_timeout_seconds", pool.pool_timeout_seconds)
    _append_key(lines, "connect_timeout_seconds", pool.connect_timeout_seconds)
    _append_key(lines, "statement_timeout_ms", pool.statement_timeout_ms)
    _append_key(lines, "pool_recycle_seconds", pool.pool_recycle_seconds)
    lines.append("")


def _append_database_section(lines: list[str], database: DatabaseConfig) -> None:
    lines.append("[database]")
    _append_key(
        lines,
        "readiness_timeout_seconds",
        database.readiness_timeout_seconds,
    )
    _append_key(lines, "retry_after_seconds", database.retry_after_seconds)
    lines.append("")
    _append_database_pool_section(lines, "api", database.api)
    _append_database_pool_section(lines, "spatial", database.spatial)
    _append_database_pool_section(lines, "worker", database.worker)


def _append_earth_engine_section(
    lines: list[str],
    earth_engine: EarthEngineConfig,
) -> None:
    lines.append("[earth_engine]")
    _append_key(lines, "project", earth_engine.project)
    if earth_engine.service_account_file != DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE:
        _append_key(
            lines,
            "service_account_file",
            earth_engine.service_account_file,
        )
    lines.append("")


def _append_logging_section(lines: list[str], logging_config: LoggingConfig) -> None:
    lines.append("[logging]")
    _append_key(lines, "level", logging_config.level)
    if logging_config.file is not None:
        _append_key(lines, "file", logging_config.file)
    lines.append("")


def _append_mcp_section(lines: list[str], mcp: McpConfig) -> None:
    lines.append("[mcp]")
    lines.append(f"enabled = {str(mcp.enabled).lower()}")
    if mcp.mount_path != DEFAULT_MCP_MOUNT_PATH:
        _append_key(lines, "mount_path", mcp.mount_path)
    lines.append("")


def _append_job_store_section(lines: list[str], job_store: JobStoreConfig) -> None:
    lines.append("[job_store]")
    _append_key(lines, "ttl_seconds", job_store.ttl_seconds)
    lines.append("")


def _append_agent_submission_limit_section(
    lines: list[str],
    submission_limit: AgentSubmissionLimitConfig,
) -> None:
    lines.append("[agent_submission_limit]")
    _append_key(lines, "limit", submission_limit.limit)
    _append_key(lines, "window_seconds", submission_limit.window_seconds)
    lines.append("")


def _append_plugins_section(lines: list[str], plugins: PluginsConfig) -> None:
    lines.append("[plugins]")
    _append_key(lines, "default_queue", plugins.default_queue)
    _append_key(lines, "allowed_queues", plugins.allowed_queues)
    if plugins.initial_repos:
        _append_key(lines, "initial_repos", plugins.initial_repos)
    if plugins.catalog_dir != DEFAULT_PLUGIN_CATALOG_DIR:
        _append_key(lines, "catalog_dir", plugins.catalog_dir)
    if plugins.runner_base_dir != DEFAULT_PLUGIN_RUNNER_BASE_DIR:
        _append_key(lines, "runner_base_dir", plugins.runner_base_dir)
    lines.append("")


def _append_workers_section(
    lines: list[str],
    workers: dict[str, WorkerConfig],
) -> None:
    for worker_name, worker in sorted(workers.items()):
        lines.append(f"[workers.{_toml_key(worker_name)}]")
        _append_key(lines, "queues", worker.queues)
        _append_key(lines, "concurrency", worker.concurrency)
        if worker.install_dir is not None:
            _append_key(lines, "install_dir", worker.install_dir)
        if worker.temp_dir is not None:
            _append_key(lines, "temp_dir", worker.temp_dir)
        lines.append("")


def render_config_toml(config: LyraConfig) -> str:
    lines: list[str] = ["schema_version = 1", ""]
    _append_api_section(lines, config.api)
    _append_redis_section(lines, config.redis)
    _append_database_section(lines, config.database)
    _append_earth_engine_section(lines, config.earth_engine)
    _append_mcp_section(lines, config.mcp)
    _append_logging_section(lines, config.logging)
    _append_job_store_section(lines, config.job_store)
    _append_agent_submission_limit_section(lines, config.agent_submission_limit)
    _append_plugins_section(lines, config.plugins)
    _append_workers_section(lines, config.workers)
    return "\n".join(lines).rstrip() + "\n"


def save_config(config: LyraConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = render_config_toml(config)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.replace(config_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


__all__ = [
    "DEFAULT_AGENT_SUBMISSION_LIMIT",
    "DEFAULT_AGENT_SUBMISSION_WINDOW_SECONDS",
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE",
    "DEFAULT_JOB_STORE_TTL_SECONDS",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_MCP_MOUNT_PATH",
    "DEFAULT_PLUGIN_CATALOG_DIR",
    "DEFAULT_PLUGIN_RUNNER_BASE_DIR",
    "DEFAULT_WORKER_CONCURRENCY",
    "LYRA_ADMIN_API_KEY_ENV",
    "LYRA_AGENT_API_KEY_ENV",
    "LYRA_DATA_DIR",
    "LYRA_POSTGRES_DB_ENV",
    "LYRA_POSTGRES_HOST_ENV",
    "LYRA_POSTGRES_PASSWORD_ENV",
    "LYRA_POSTGRES_PORT_ENV",
    "LYRA_POSTGRES_USER_ENV",
    "AdminConfig",
    "AgentConfig",
    "AgentSubmissionLimitConfig",
    "ApiConfig",
    "ConfigLoadError",
    "ConfigSecretError",
    "DatabaseConfig",
    "EarthEngineConfig",
    "JobStoreConfig",
    "LoggingConfig",
    "LyraConfig",
    "McpConfig",
    "PluginsConfig",
    "RedisConfig",
    "WorkerConfig",
    "clear_config_cache",
    "ensure_runtime_directories",
    "get_config",
    "get_config_path",
    "load_config",
    "read_scalar_env_var",
    "read_scalar_secret_file",
    "reload_config",
    "render_config_toml",
    "require_nonempty_file",
    "save_config",
    "validate_config_secret_references",
]
