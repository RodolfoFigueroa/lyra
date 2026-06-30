from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LYRA_DATA_DIR = Path("/lyra_data")
DEFAULT_CONFIG_PATH = LYRA_DATA_DIR / "config" / "lyra.toml"
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 5219
DEFAULT_JOB_STORE_TTL_SECONDS = 600
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_CONCURRENCY = 1

_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_ALLOWED_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


class ConfigSecretError(RuntimeError):
    """Raised when a secret reference cannot be resolved to a usable value."""


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _strip_required_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        msg = "value must be a non-empty string"
        raise ValueError(msg)
    return stripped


def _strip_optional_path(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        msg = "path must be a non-empty string"
        raise ValueError(msg)
    return stripped


def _validate_absolute_path(path: Path | None) -> Path | None:
    if path is not None and not path.is_absolute():
        msg = "path must be absolute"
        raise ValueError(msg)
    return path


def _strip_string_list(value: Any) -> Any:
    if not isinstance(value, list):
        return value

    return [_strip_required_string(item) for item in value]


def _strip_string_mapping(value: Any, *, value_label: str) -> Any:
    if not isinstance(value, dict):
        return value

    stripped_items: dict[Any, Any] = {}
    for raw_key, raw_value in value.items():
        key = _strip_required_string(raw_key)
        if key in stripped_items:
            msg = f"duplicate {value_label} key after trimming whitespace: {key!r}"
            raise ValueError(msg)
        stripped_items[key] = _strip_required_string(raw_value)
    return stripped_items


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


class ApiConfig(StrictConfigModel):
    host: str = Field(default=DEFAULT_API_HOST)
    port: int = Field(default=DEFAULT_API_PORT, ge=1, le=65535)

    @field_validator("host", mode="before")
    @classmethod
    def validate_host(cls, value: Any) -> Any:
        return _strip_required_string(value)


class RedisConfig(StrictConfigModel):
    url: str = Field(min_length=1)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value: Any) -> Any:
        return _strip_required_string(value)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in _ALLOWED_REDIS_SCHEMES or not parsed.netloc:
            msg = "redis.url must be a redis:// or rediss:// URL"
            raise ValueError(msg)
        return value


class DatabaseConfig(StrictConfigModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    name: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password_file: Path

    @field_validator("host", "name", "user", mode="before")
    @classmethod
    def validate_required_strings(cls, value: Any) -> Any:
        return _strip_required_string(value)

    @field_validator("password_file", mode="before")
    @classmethod
    def normalize_password_file(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("password_file")
    @classmethod
    def validate_password_file(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "database.password_file is required"
            raise ValueError(msg)
        return path

    def read_password(self) -> str:
        return read_scalar_secret_file(
            self.password_file,
            field_name="database.password_file",
        )


class EarthEngineConfig(StrictConfigModel):
    project: str = Field(min_length=1)
    service_account_file: Path

    @field_validator("project", mode="before")
    @classmethod
    def validate_project(cls, value: Any) -> Any:
        return _strip_required_string(value)

    @field_validator("service_account_file", mode="before")
    @classmethod
    def normalize_service_account_file(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("service_account_file")
    @classmethod
    def validate_service_account_file(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "earth_engine.service_account_file is required"
            raise ValueError(msg)
        return path


class AdminConfig(StrictConfigModel):
    api_key_file: Path

    @field_validator("api_key_file", mode="before")
    @classmethod
    def normalize_api_key_file(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("api_key_file")
    @classmethod
    def validate_api_key_file(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "admin.api_key_file is required"
            raise ValueError(msg)
        return path

    def read_api_key(self) -> str:
        return read_scalar_secret_file(
            self.api_key_file,
            field_name="admin.api_key_file",
        )


class LoggingConfig(StrictConfigModel):
    level: str = Field(default=DEFAULT_LOG_LEVEL)
    file: Path | None = None

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: Any) -> Any:
        value = _strip_required_string(value)
        return value.upper() if isinstance(value, str) else value

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        if value not in _ALLOWED_LOG_LEVELS:
            levels = ", ".join(sorted(_ALLOWED_LOG_LEVELS))
            msg = f"logging.level must be one of: {levels}"
            raise ValueError(msg)
        return value

    @field_validator("file", mode="before")
    @classmethod
    def normalize_file(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: Path | None) -> Path | None:
        return _validate_absolute_path(value)


class JobStoreConfig(StrictConfigModel):
    ttl_seconds: int = Field(default=DEFAULT_JOB_STORE_TTL_SECONDS, gt=0)


class PluginsConfig(StrictConfigModel):
    repos: list[str]
    catalog_dir: Path
    runner_base_dir: Path
    default_queue: str = Field(min_length=1)
    allowed_queues: list[str] = Field(min_length=1)
    metric_queues: dict[str, str] = Field(default_factory=dict)

    @field_validator("repos", "allowed_queues", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: Any) -> Any:
        return _strip_string_list(value)

    @field_validator("catalog_dir", "runner_base_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("catalog_dir", "runner_base_dir")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        path = _validate_absolute_path(value)
        if path is None:
            msg = "plugin path fields are required"
            raise ValueError(msg)
        return path

    @field_validator("default_queue", mode="before")
    @classmethod
    def normalize_default_queue(cls, value: Any) -> Any:
        return _strip_required_string(value)

    @field_validator("metric_queues", mode="before")
    @classmethod
    def normalize_metric_queues(cls, value: Any) -> Any:
        return _strip_string_mapping(value, value_label="metric queue")

    @field_validator("repos")
    @classmethod
    def validate_unique_repos(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for repo in value:
            if repo in seen:
                duplicates.add(repo)
            seen.add(repo)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            msg = (
                "duplicate plugin repository entries after trimming whitespace: "
                f"{names}"
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_queues(self) -> Self:
        allowed_queues = set(self.allowed_queues)
        if self.default_queue not in allowed_queues:
            msg = "plugins.default_queue must appear in plugins.allowed_queues"
            raise ValueError(msg)

        invalid_assignments = sorted(
            {
                queue
                for queue in self.metric_queues.values()
                if queue not in allowed_queues
            }
        )
        if invalid_assignments:
            names = ", ".join(invalid_assignments)
            msg = (
                "plugins.metric_queues values must appear in "
                f"plugins.allowed_queues: {names}"
            )
            raise ValueError(msg)
        return self


class WorkerConfig(StrictConfigModel):
    queues: list[str] = Field(min_length=1)
    concurrency: int = Field(default=DEFAULT_WORKER_CONCURRENCY, gt=0)
    install_dir: Path | None = None
    temp_dir: Path | None = None

    @field_validator("queues", mode="before")
    @classmethod
    def normalize_queues(cls, value: Any) -> Any:
        return _strip_string_list(value)

    @field_validator("install_dir", "temp_dir", mode="before")
    @classmethod
    def normalize_paths(cls, value: Any) -> Any:
        return _strip_optional_path(value)

    @field_validator("install_dir", "temp_dir")
    @classmethod
    def validate_paths(cls, value: Path | None) -> Path | None:
        return _validate_absolute_path(value)


class LyraConfig(StrictConfigModel):
    schema_version: Literal[1]
    api: ApiConfig
    redis: RedisConfig
    database: DatabaseConfig
    earth_engine: EarthEngineConfig
    admin: AdminConfig
    logging: LoggingConfig
    job_store: JobStoreConfig
    plugins: PluginsConfig
    workers: dict[str, WorkerConfig] = Field(min_length=1)

    @field_validator("workers", mode="before")
    @classmethod
    def normalize_workers(cls, value: Any) -> Any:
        return _strip_string_mapping(value, value_label="worker")

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
        if not isinstance(worker_name, str):
            msg = "worker name must be a string"
            raise TypeError(msg)
        try:
            return self.workers[worker_name]
        except KeyError as exc:
            msg = f"unknown worker config: {worker_name}"
            raise KeyError(msg) from exc

    def worker_install_dir(self, name: str) -> Path:
        worker_name = _strip_required_string(name)
        if not isinstance(worker_name, str):
            msg = "worker name must be a string"
            raise TypeError(msg)
        worker = self.get_worker(worker_name)
        return worker.install_dir or self.plugins.runner_base_dir / worker_name

    def worker_temp_dir(self, name: str) -> Path:
        worker_name = _strip_required_string(name)
        if not isinstance(worker_name, str):
            msg = "worker name must be a string"
            raise TypeError(msg)
        worker = self.get_worker(worker_name)
        return worker.temp_dir or LYRA_DATA_DIR / "cache" / "jobs" / worker_name


__all__ = [
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_JOB_STORE_TTL_SECONDS",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_WORKER_CONCURRENCY",
    "LYRA_DATA_DIR",
    "AdminConfig",
    "ApiConfig",
    "ConfigSecretError",
    "DatabaseConfig",
    "EarthEngineConfig",
    "JobStoreConfig",
    "LoggingConfig",
    "LyraConfig",
    "PluginsConfig",
    "RedisConfig",
    "WorkerConfig",
    "read_scalar_secret_file",
]
