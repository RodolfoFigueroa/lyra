from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from lyra_app.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_EARTH_ENGINE_SERVICE_ACCOUNT_FILE,
    DEFAULT_MCP_MOUNT_PATH,
    DEFAULT_PLUGIN_CATALOG_DIR,
    DEFAULT_PLUGIN_RUNNER_BASE_DIR,
    AgentSubmissionLimitConfig,
    ApiConfig,
    DatabaseConfig,
    DatabasePoolConfig,
    EarthEngineConfig,
    JobEventsConfig,
    JobStoreConfig,
    LoggingConfig,
    LyraConfig,
    McpConfig,
    PluginsConfig,
    RedisConfig,
    WorkerConfig,
)

_BARE_TOML_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


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
    for name in ("host", "port", "name", "user"):
        _append_key(lines, name, getattr(database, name))
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
    lines.extend(("[mcp]", f"enabled = {str(mcp.enabled).lower()}"))
    if mcp.mount_path != DEFAULT_MCP_MOUNT_PATH:
        _append_key(lines, "mount_path", mcp.mount_path)
    lines.append("")


def _append_job_store_section(lines: list[str], job_store: JobStoreConfig) -> None:
    lines.append("[job_store]")
    _append_key(lines, "ttl_seconds", job_store.ttl_seconds)
    lines.append("")


def _append_job_events_section(lines: list[str], job_events: JobEventsConfig) -> None:
    lines.append("[job_events]")
    _append_key(
        lines,
        "progress_min_interval_ms",
        job_events.progress_min_interval_ms,
    )
    _append_key(lines, "max_events_per_second", job_events.max_events_per_second)
    _append_key(lines, "max_payload_bytes", job_events.max_payload_bytes)
    _append_key(lines, "max_stream_events", job_events.max_stream_events)
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
    if plugins.catalog_dir != DEFAULT_PLUGIN_CATALOG_DIR:
        _append_key(lines, "catalog_dir", plugins.catalog_dir)
    if plugins.runner_base_dir != DEFAULT_PLUGIN_RUNNER_BASE_DIR:
        _append_key(lines, "runner_base_dir", plugins.runner_base_dir)
    lines.append("")

    for repo in plugins.repos:
        lines.extend(
            [
                "[[plugins.repos]]",
                f"id = {_toml_string(repo.id)}",
                f"source = {_toml_string(repo.source)}",
                f"enabled = {str(repo.enabled).lower()}",
            ]
        )
        if repo.ref is not None:
            lines.append(f"ref = {_toml_string(repo.ref)}")
        if repo.routing:
            lines.append("[plugins.repos.routing]")
            for metric, queue in repo.routing.items():
                lines.append(f"{_toml_key(metric)} = {_toml_string(queue)}")
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
    """Serialize a validated runtime configuration as canonical TOML.

    Returns:
        A deterministic TOML document ending in a newline.
    """
    lines: list[str] = ["schema_version = 2", ""]
    _append_api_section(lines, config.api)
    _append_redis_section(lines, config.redis)
    _append_database_section(lines, config.database)
    _append_earth_engine_section(lines, config.earth_engine)
    _append_mcp_section(lines, config.mcp)
    _append_logging_section(lines, config.logging)
    _append_job_store_section(lines, config.job_store)
    _append_job_events_section(lines, config.job_events)
    _append_agent_submission_limit_section(lines, config.agent_submission_limit)
    _append_plugins_section(lines, config.plugins)
    _append_workers_section(lines, config.workers)
    return "\n".join(lines).rstrip() + "\n"


def save_config(config: LyraConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Atomically write a validated runtime configuration as TOML."""
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
