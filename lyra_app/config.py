from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

LYRA_DATA_DIR = Path("/lyra_data")
DEFAULT_CONFIG_PATH = LYRA_DATA_DIR / "config" / "lyra.toml"
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 5219
DEFAULT_JOB_STORE_TTL_SECONDS = 600
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_CONCURRENCY = 1
DEFAULT_LOG_DIR = LYRA_DATA_DIR / "logs"
DEFAULT_DATABASE_PASSWORD_FILE = LYRA_DATA_DIR / "secrets" / "postgres_password"
DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE = (
    LYRA_DATA_DIR / "secrets" / "service-account.json"
)
DEFAULT_ADMIN_API_KEY_FILE = LYRA_DATA_DIR / "secrets" / "admin_api_key"
DEFAULT_PLUGIN_CATALOG_DIR = LYRA_DATA_DIR / "plugins" / "catalog"
DEFAULT_PLUGIN_RUNNER_BASE_DIR = LYRA_DATA_DIR / "plugins" / "runners"

_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})
_ALLOWED_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())
_BARE_TOML_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigSecretError(RuntimeError):
    """Raised when a secret reference cannot be resolved to a usable value."""


class ConfigLoadError(RuntimeError):
    """Raised when the TOML config file cannot be loaded or validated."""


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_CONFIG_CACHE: LyraConfig | None = None
_CONFIG_CACHE_PATH: Path | None = None


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
    password_file: Path = DEFAULT_DATABASE_PASSWORD_FILE

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
    service_account_file: Path = DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE

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
    api_key_file: Path = DEFAULT_ADMIN_API_KEY_FILE

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
    catalog_dir: Path = DEFAULT_PLUGIN_CATALOG_DIR
    runner_base_dir: Path = DEFAULT_PLUGIN_RUNNER_BASE_DIR
    default_queue: str = Field(min_length=1)
    allowed_queues: list[str] = Field(min_length=1)

    @field_validator("allowed_queues", mode="before")
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

    @model_validator(mode="after")
    def validate_queues(self) -> Self:
        allowed_queues = set(self.allowed_queues)
        if self.default_queue not in allowed_queues:
            msg = "plugins.default_queue must appear in plugins.allowed_queues"
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


def validate_config_secret_references(config: LyraConfig) -> None:
    config.database.read_password()
    config.admin.read_api_key()
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


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LyraConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as config_file:
            raw_config = tomllib.load(config_file)
    except FileNotFoundError as exc:
        msg = f"Lyra config file does not exist: {config_path}"
        raise ConfigLoadError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"Lyra config file is not valid TOML: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc
    except OSError as exc:
        msg = f"Lyra config file could not be read: {config_path}"
        raise ConfigLoadError(msg) from exc

    try:
        config = LyraConfig.model_validate(raw_config)
        validate_config_secret_references(config)
    except ValidationError as exc:
        msg = f"Lyra config file failed validation: {config_path}: {exc}"
        raise ConfigLoadError(msg) from exc
    except ConfigSecretError as exc:
        msg = f"Lyra config file references invalid secret files: {config_path}: {exc}"
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
    value: str | Path | int | list[str],
) -> None:
    if isinstance(value, int):
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
    lines.append("")


def _append_redis_section(lines: list[str], redis: RedisConfig) -> None:
    lines.append("[redis]")
    _append_key(lines, "url", redis.url)
    lines.append("")


def _append_database_section(lines: list[str], database: DatabaseConfig) -> None:
    lines.append("[database]")
    _append_key(lines, "host", database.host)
    _append_key(lines, "port", database.port)
    _append_key(lines, "name", database.name)
    _append_key(lines, "user", database.user)
    if database.password_file != DEFAULT_DATABASE_PASSWORD_FILE:
        _append_key(lines, "password_file", database.password_file)
    lines.append("")


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


def _append_admin_section(lines: list[str], admin: AdminConfig) -> None:
    lines.append("[admin]")
    if admin.api_key_file != DEFAULT_ADMIN_API_KEY_FILE:
        _append_key(lines, "api_key_file", admin.api_key_file)
    lines.append("")


def _append_logging_section(lines: list[str], logging_config: LoggingConfig) -> None:
    lines.append("[logging]")
    _append_key(lines, "level", logging_config.level)
    if logging_config.file is not None:
        _append_key(lines, "file", logging_config.file)
    lines.append("")


def _append_job_store_section(lines: list[str], job_store: JobStoreConfig) -> None:
    lines.append("[job_store]")
    _append_key(lines, "ttl_seconds", job_store.ttl_seconds)
    lines.append("")


def _append_plugins_section(lines: list[str], plugins: PluginsConfig) -> None:
    lines.append("[plugins]")
    _append_key(lines, "default_queue", plugins.default_queue)
    _append_key(lines, "allowed_queues", plugins.allowed_queues)
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
    _append_admin_section(lines, config.admin)
    _append_logging_section(lines, config.logging)
    _append_job_store_section(lines, config.job_store)
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
    "DEFAULT_ADMIN_API_KEY_FILE",
    "DEFAULT_API_HOST",
    "DEFAULT_API_PORT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DATABASE_PASSWORD_FILE",
    "DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE",
    "DEFAULT_JOB_STORE_TTL_SECONDS",
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_PLUGIN_CATALOG_DIR",
    "DEFAULT_PLUGIN_RUNNER_BASE_DIR",
    "DEFAULT_WORKER_CONCURRENCY",
    "LYRA_DATA_DIR",
    "AdminConfig",
    "ApiConfig",
    "ConfigLoadError",
    "ConfigSecretError",
    "DatabaseConfig",
    "EarthEngineConfig",
    "JobStoreConfig",
    "LoggingConfig",
    "LyraConfig",
    "PluginsConfig",
    "RedisConfig",
    "WorkerConfig",
    "clear_config_cache",
    "ensure_runtime_directories",
    "get_config",
    "get_config_path",
    "load_config",
    "read_scalar_secret_file",
    "reload_config",
    "render_config_toml",
    "require_nonempty_file",
    "save_config",
    "validate_config_secret_references",
]
