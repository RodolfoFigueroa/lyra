"""Offline configuration schema shared by the server and administrative CLI."""

from __future__ import annotations

import ipaddress
import logging
import tomllib
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse, urlsplit

from lyra.sdk.plugin_sources import PluginSource, PluginSourceKind, parse_plugin_source
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LYRA_DATA_DIR = Path("/lyra_data")
DEFAULT_CONFIG_PATH = LYRA_DATA_DIR / "config" / "lyra.toml"
DEFAULT_API_HOST = str(ipaddress.IPv4Address(0))
DEFAULT_API_PORT = 5219
DEFAULT_FORWARDED_ALLOW_IPS = ["127.0.0.1"]
DEFAULT_RESULT_RETENTION_SECONDS = 86400
DEFAULT_PROGRESS_MIN_INTERVAL_MS = 1000
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
DEFAULT_MCP_MOUNT_PATH = "/mcp"

_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_ALLOWED_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


class StrictConfigModel(BaseModel):
    """Reject unknown configuration keys."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


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


class ApiConfig(StrictConfigModel):
    """Configure the HTTP server listener, public URL, and trusted proxies."""

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
        """Strip whitespace and reject blank API string settings.

        Returns:
            The normalized, nonblank setting value.
        """
        return _strip_required_string(value)

    @field_validator("forwarded_allow_ips")
    @classmethod
    def normalize_forwarded_allow_ips(cls, value: list[str]) -> list[str]:
        """Strip and validate every trusted proxy address or CIDR.

        Returns:
            A list containing the normalized proxy entries.
        """
        return _strip_string_list(value)

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        """Validate and normalize the externally reachable HTTP base URL.

        Returns:
            The validated base URL without a trailing slash.

        Raises:
            ValueError: If the URL is malformed, contains credentials or URL
                suffixes, uses an internal hostname, or uses insecure HTTP for a
                non-loopback host.
        """
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
    """Configure the Redis endpoint used for queues and retained job state."""

    url: str = Field(
        min_length=1,
        description="Redis URL used by Celery and the retained job store.",
    )

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        """Strip surrounding whitespace from the Redis URL.

        Returns:
            The normalized, nonblank Redis URL.
        """
        return _strip_required_string(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Require a complete Redis URL with a supported scheme.

        Returns:
            The validated Redis URL unchanged.

        Raises:
            ValueError: If the URL lacks a host or does not use ``redis`` or
                ``rediss``.
        """
        parsed = urlparse(value)
        if parsed.scheme not in _ALLOWED_REDIS_SCHEMES or not parsed.netloc:
            msg = "redis.url must be a redis:// or rediss:// URL"
            raise ValueError(msg)
        return value


class DatabasePoolConfig(StrictConfigModel):
    """Configure connection-pool limits and PostgreSQL timeouts."""

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
    """Configure PostgreSQL credentials, readiness, and workload pools."""

    host: str = Field(min_length=1, description="PostgreSQL host.")
    port: int = Field(default=5432, ge=1, le=65535, description="PostgreSQL port.")
    name: str = Field(min_length=1, description="PostgreSQL database name.")
    user: str = Field(min_length=1, description="PostgreSQL user.")
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

    @field_validator("host", "name", "user")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        """Strip and reject blank database connection fields.

        Returns:
            The normalized, nonblank connection value.
        """
        return _strip_required_string(value)


class EarthEngineConfig(StrictConfigModel):
    """Configure the Earth Engine project and service-account file."""

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
        """Strip and reject a blank Earth Engine project identifier.

        Returns:
            The normalized, nonblank project identifier.
        """
        return _strip_required_string(value)

    @field_validator("service_account_file")
    @classmethod
    def validate_service_account_file(cls, value: Path) -> Path:
        """Require an absolute service-account file path.

        Returns:
            The validated absolute path.

        Raises:
            ValueError: If no service-account path is supplied.
        """
        path = _validate_absolute_path(value)
        if path is None:
            msg = "earth_engine.service_account_file is required"
            raise ValueError(msg)
        return path


class McpConfig(StrictConfigModel):
    """Configure whether and where the MCP server is mounted."""

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
        """Strip and reject a blank MCP mount path.

        Returns:
            The normalized, nonblank mount path.
        """
        return _strip_required_string(value)

    @field_validator("mount_path")
    @classmethod
    def validate_mount_path(cls, value: str) -> str:
        """Require an absolute mount path without a trailing slash.

        Returns:
            The validated mount path unchanged.

        Raises:
            ValueError: If the path is not absolute or has a trailing slash.
        """
        if not value.startswith("/"):
            msg = "mcp.mount_path must start with /"
            raise ValueError(msg)
        if len(value) > 1 and value.endswith("/"):
            msg = "mcp.mount_path must not end with /"
            raise ValueError(msg)
        return value


class LoggingConfig(StrictConfigModel):
    """Configure application log severity and optional file output."""

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
        """Normalize a logging level name to uppercase.

        Returns:
            The stripped, uppercase logging level name.
        """
        value = _strip_required_string(value)
        return value.upper()

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """Require a logging level recognized by the standard library.

        Returns:
            The recognized logging level name unchanged.

        Raises:
            ValueError: If the standard library does not recognize the level.
        """
        if value not in _ALLOWED_LOG_LEVELS:
            levels = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            msg = f"logging.level must be one of: {levels}"
            raise ValueError(msg)
        return value

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: Path | None) -> Path | None:
        """Require the optional log file path to be absolute.

        Returns:
            The absolute log path, or ``None`` when file logging is disabled.
        """
        return _validate_absolute_path(value)


class JobStoreConfig(StrictConfigModel):
    """Configure retention for persisted job data."""

    result_retention_seconds: int = Field(
        default=DEFAULT_RESULT_RETENTION_SECONDS,
        gt=0,
        description=(
            "Retention after termination for status, results, provenance, "
            "and idempotency."
        ),
    )


class JobProgressConfig(StrictConfigModel):
    """Configure optional progress snapshot writes."""

    min_interval_ms: int = Field(
        default=DEFAULT_PROGRESS_MIN_INTERVAL_MS,
        ge=0,
        strict=True,
        description="Minimum elapsed time between progress writes.",
    )


class AgentSubmissionLimitConfig(StrictConfigModel):
    """Configure the shared fixed-window agent submission limit."""

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


class PluginRepoConfig(StrictConfigModel):
    """Declare one plugin source and its optional metric routing overrides."""

    id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$",
        description="Stable repository identifier.",
    )
    source: str = Field(
        min_length=1,
        description=(
            "GitHub owner/repository or HTTPS URL, absolute file:// Git repository, "
            "or absolute dir:// directory. Revisions belong in ref."
        ),
    )
    ref: str | None = Field(
        default=None,
        min_length=1,
        description="Git branch, tag, or commit; omitted selects the default branch.",
    )
    enabled: bool = Field(
        default=True, description="Include this repository in the startup catalog."
    )
    routing: dict[str, str] = Field(
        default_factory=dict,
        description="Metric names mapped to explicit queues within this repository.",
    )

    @property
    def parsed_source(self) -> PluginSource:
        """The source interpreted by the shared offline parser."""
        return parse_plugin_source(self.source)

    @property
    def source_kind(self) -> PluginSourceKind:
        """The source transport inferred without accessing the source."""
        return self.parsed_source.kind

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        """Validate source syntax and routing strings without filesystem access.

        Returns:
            The validated repository declaration.

        Raises:
            ValueError: If a source, revision, or routing value is invalid.
        """
        if self.id != self.id.strip():
            msg = "repository IDs must not contain surrounding whitespace"
            raise ValueError(msg)
        source = self.parsed_source
        self.source = source.canonical
        if source.kind == "directory" and self.ref is not None:
            msg = "directory sources cannot specify ref"
            raise ValueError(msg)
        if self.ref is not None and (
            not self.ref.strip()
            or self.ref != self.ref.strip()
            or self.ref.startswith("-")
            or any(c.isspace() for c in self.ref)
        ):
            msg = (
                "ref must be a nonblank Git revision without whitespace "
                "or a leading dash"
            )
            raise ValueError(msg)
        for metric, queue in self.routing.items():
            if (
                not metric.strip()
                or metric != metric.strip()
                or not queue.strip()
                or queue != queue.strip()
            ):
                msg = (
                    "routing metric names and queues must be nonblank without "
                    "surrounding whitespace"
                )
                raise ValueError(msg)
        return self


class PluginsConfig(StrictConfigModel):
    """Configure plugin storage locations, sources, and routing queues."""

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
    repos: list[PluginRepoConfig] = Field(
        default_factory=list,
        description="Authoritative plugin declarations and per-repository routing.",
    )

    @model_validator(mode="after")
    def validate_repositories(self) -> Self:
        """Check repository identities and override queues.

        Returns:
            The validated plugin configuration.

        Raises:
            ValueError: If identities, sources, or queue assignments conflict.
        """
        ids: set[str] = set()
        sources: set[str] = set()
        for repo in self.repos:
            if repo.id in ids or (repo.enabled and repo.source in sources):
                msg = "repository IDs and enabled sources must be unique"
                raise ValueError(msg)
            ids.add(repo.id)
            if repo.enabled:
                sources.add(repo.source)
            if set(repo.routing.values()) - set(self.allowed_queues):
                msg = "routing queues must appear in plugins.allowed_queues"
                raise ValueError(msg)
        return self

    @field_validator("allowed_queues")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        """Validate allowed queue names.

        Returns:
            Normalized queue names.
        """
        return _strip_string_list(value)

    @field_validator("catalog_dir", "runner_base_dir")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        """Require plugin catalog and runner directories to be absolute.

        Returns:
            The validated absolute directory path.

        Raises:
            ValueError: If a required plugin directory is absent.
        """
        path = _validate_absolute_path(value)
        if path is None:
            msg = "plugin path fields are required"
            raise ValueError(msg)
        return path

    @field_validator("default_queue")
    @classmethod
    def normalize_default_queue(cls, value: str) -> str:
        """Strip and reject a blank default queue name.

        Returns:
            The normalized, nonblank queue name.
        """
        return _strip_required_string(value)

    @model_validator(mode="after")
    def validate_queues(self) -> Self:
        """Require the default queue to be included among allowed queues.

        Returns:
            This configuration after validating its queue relationship.

        Raises:
            ValueError: If the default queue is not an allowed queue.
        """
        allowed_queues = set(self.allowed_queues)
        if self.default_queue not in allowed_queues:
            msg = "plugins.default_queue must appear in plugins.allowed_queues"
            raise ValueError(msg)

        return self


class WorkerConfig(StrictConfigModel):
    """Configure one named worker pool and its runtime directories."""

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
        """Strip and reject blank worker queue names.

        Returns:
            A list containing the normalized, nonblank queue names.
        """
        return _strip_string_list(value)

    @field_validator("install_dir", "temp_dir")
    @classmethod
    def validate_paths(cls, value: Path | None) -> Path | None:
        """Require optional worker directories to be absolute.

        Returns:
            The absolute directory path, or ``None`` when no override is set.
        """
        return _validate_absolute_path(value)


class LyraConfig(StrictConfigModel):
    """Represent the complete validated runtime configuration for Lyra."""

    schema_version: Literal[2] = Field(
        description="Server configuration schema version."
    )
    api: ApiConfig = Field(description="API bind and public URL settings.")
    redis: RedisConfig = Field(description="Redis connection settings.")
    database: DatabaseConfig = Field(
        description="PostgreSQL connection and pool settings.",
    )
    earth_engine: EarthEngineConfig = Field(
        description="Google Earth Engine credentials and project settings."
    )
    mcp: McpConfig = Field(
        default_factory=McpConfig,
        description="MCP transport settings.",
    )
    logging: LoggingConfig = Field(description="Application logging settings.")
    job_store: JobStoreConfig = Field(description="Retained job-store settings.")
    job_progress: JobProgressConfig = Field(
        default_factory=JobProgressConfig,
        description="Progress snapshot coalescing settings.",
    )
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
        """Require every worker queue to be declared in the plugin settings.

        Returns:
            This configuration after validating every worker queue.

        Raises:
            ValueError: If any worker consumes a queue that is not allowed.
        """
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
        """Return a named worker configuration or raise for an unknown name.

        Returns:
            The configuration registered under the normalized worker name.

        Raises:
            KeyError: If the configuration has no worker with that name.
        """
        worker_name = _strip_required_string(name)
        try:
            return self.workers[worker_name]
        except KeyError as exc:
            msg = f"unknown worker config: {worker_name}"
            raise KeyError(msg) from exc

    def worker_install_dir(self, name: str) -> Path:
        """Resolve the effective plugin installation directory for a worker.

        Returns:
            The worker-specific override or its directory below the runner base.
        """
        worker_name = _strip_required_string(name)
        worker = self.get_worker(worker_name)
        return worker.install_dir or self.plugins.runner_base_dir / worker_name

    def worker_temp_dir(self, name: str) -> Path:
        """Resolve the effective per-job temporary directory for a worker.

        Returns:
            The worker-specific override or its default job-cache directory.
        """
        worker_name = _strip_required_string(name)
        worker = self.get_worker(worker_name)
        return worker.temp_dir or LYRA_DATA_DIR / "cache" / "jobs" / worker_name


def load_config(path: str | Path) -> LyraConfig:
    """Read and validate configuration without resolving secrets or plugins.

    Returns:
        The validated configuration.
    """
    with Path(path).open("rb") as source:
        return LyraConfig.model_validate(tomllib.load(source))
