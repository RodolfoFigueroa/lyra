from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from lyra_app import config as config_module
from lyra_app.config import (
    LYRA_ADMIN_API_KEY_ENV,
    LYRA_AGENT_API_KEY_ENV,
    LYRA_POSTGRES_DB_ENV,
    LYRA_POSTGRES_HOST_ENV,
    LYRA_POSTGRES_PASSWORD_ENV,
    LYRA_POSTGRES_PORT_ENV,
    LYRA_POSTGRES_USER_ENV,
    ConfigLoadError,
    LyraConfig,
    clear_config_cache,
    ensure_runtime_directories,
    get_config,
    load_config,
    reload_config,
    render_config_toml,
    save_config,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _q(value: object) -> str:
    return json.dumps(str(value))


def _write_secret_files(base: Path) -> dict[str, Path]:
    secrets_dir = base / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    paths = {
        "service_account": secrets_dir / "service-account.json",
    }
    paths["service_account"].write_text(
        '{"client_email":"test@example.com"}', encoding="utf-8"
    )
    return paths


def _valid_toml(
    base: Path,
    *,
    api_port: int = 5219,
    worker_name: str = "interactive",
) -> str:
    secrets = _write_secret_files(base)
    return (
        f"""
schema_version = 1

[api]
host = "0.0.0.0"
port = {api_port}
public_base_url = "http://127.0.0.1:{api_port}"

[redis]
url = "redis://redis:6379/0"

[earth_engine]
project = "earth-engine-project"
service_account_file = {_q(secrets["service_account"])}

[logging]
level = "INFO"
file = {_q(base / "logs" / "lyra.log")}

[job_store]
ttl_seconds = 600

[agent_submission_limit]
limit = 10
window_seconds = 60

[plugins]
catalog_dir = {_q(base / "plugins" / "catalog")}
runner_base_dir = {_q(base / "plugins" / "runners")}
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]

[workers.{_q(worker_name)}]
queues = ["interactive"]
concurrency = 32
install_dir = {_q(base / "plugins" / "runners" / worker_name)}
temp_dir = {_q(base / "cache" / "jobs" / worker_name)}
""".strip()
        + "\n"
    )


def _write_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


@pytest.fixture(autouse=True)
def _runtime_config_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(LYRA_POSTGRES_HOST_ENV, "postgres")
    monkeypatch.setenv(LYRA_POSTGRES_PORT_ENV, "5432")
    monkeypatch.setenv(LYRA_POSTGRES_DB_ENV, "lyra")
    monkeypatch.setenv(LYRA_POSTGRES_USER_ENV, "lyra")
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, "postgres-secret")
    monkeypatch.setenv(LYRA_ADMIN_API_KEY_ENV, "admin-secret")
    monkeypatch.setenv(LYRA_AGENT_API_KEY_ENV, "agent-secret")
    clear_config_cache()
    yield
    clear_config_cache()


def test_load_config_reads_toml_and_validates_secret_references(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path))

    config = load_config(config_path)

    assert config.api.port == 5219
    assert config.database.read_password() == "postgres-secret"
    assert config.admin.read_api_key() == "admin-secret"
    assert config.agent.read_api_key() == "agent-secret"
    assert config.agent_submission_limit.limit == 10
    assert config.agent_submission_limit.window_seconds == 60
    assert config.earth_engine.service_account_file.exists()
    assert config.plugins.allowed_queues == ["interactive", "batch"]
    assert config.plugins.initial_repos == []


def test_load_and_render_config_preserves_initial_plugin_repos(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    contents = _valid_toml(tmp_path).replace(
        'allowed_queues = ["interactive", "batch"]',
        'allowed_queues = ["interactive", "batch"]\n'
        'initial_repos = ["owner/plugin@main", "owner/other-plugin"]',
    )
    _write_config(config_path, contents)

    config = load_config(config_path)
    rendered = render_config_toml(config)
    reparsed = LyraConfig.model_validate(config_module.tomllib.loads(rendered))

    assert config.plugins.initial_repos == [
        "owner/plugin@main",
        "owner/other-plugin",
    ]
    assert reparsed.plugins.initial_repos == config.plugins.initial_repos


def test_config_rejects_duplicate_initial_plugin_repos(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    contents = _valid_toml(tmp_path).replace(
        'allowed_queues = ["interactive", "batch"]',
        'allowed_queues = ["interactive", "batch"]\n'
        'initial_repos = ["owner/plugin", "owner/plugin@main"]',
    )
    _write_config(config_path, contents)

    with pytest.raises(ConfigLoadError, match="duplicate plugin repo IDs"):
        load_config(config_path)


def test_load_config_reads_read_only_config_file_mount_shape(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    contents = _valid_toml(tmp_path)
    _write_config(config_path, contents)
    config_path.chmod(0o444)

    config = load_config(config_path)

    assert config.api.port == 5219
    assert config_path.read_text(encoding="utf-8") == contents


def test_load_config_fails_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError, match="does not exist"):
        load_config(tmp_path / "config" / "missing.toml")


def test_load_config_fails_for_invalid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, "[api\nport = 5219\n")

    with pytest.raises(ConfigLoadError, match="not valid TOML"):
        load_config(config_path)


def test_load_config_fails_for_schema_validation_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path) + "\nunexpected = true\n")

    with pytest.raises(ConfigLoadError, match="failed validation"):
        load_config(config_path)


def test_load_config_rejects_env_backed_toml_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    contents = _valid_toml(tmp_path).replace(
        "[earth_engine]",
        '[database]\nhost = "postgres"\n\n[earth_engine]',
    )
    _write_config(config_path, contents)

    with pytest.raises(ConfigLoadError, match=r"\[database\]"):
        load_config(config_path)


def test_load_config_fails_for_missing_admin_api_key_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path))
    monkeypatch.delenv(LYRA_ADMIN_API_KEY_ENV)

    with pytest.raises(ConfigLoadError, match=LYRA_ADMIN_API_KEY_ENV):
        load_config(config_path)


def test_load_config_fails_for_missing_agent_api_key_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path))
    monkeypatch.delenv(LYRA_AGENT_API_KEY_ENV)

    with pytest.raises(ConfigLoadError, match=LYRA_AGENT_API_KEY_ENV):
        load_config(config_path)


def test_load_config_fails_for_empty_postgres_password_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path))
    monkeypatch.setenv(LYRA_POSTGRES_PASSWORD_ENV, "\n")

    with pytest.raises(ConfigLoadError, match=LYRA_POSTGRES_PASSWORD_ENV):
        load_config(config_path)


def test_load_config_fails_for_empty_service_account_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    contents = _valid_toml(tmp_path)
    (tmp_path / "secrets" / "service-account.json").write_text(" ", encoding="utf-8")
    _write_config(config_path, contents)

    with pytest.raises(ConfigLoadError, match=r"earth_engine\.service_account_file"):
        load_config(config_path)


def test_get_config_caches_until_explicit_reload(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path, api_port=5219))

    first = get_config(config_path)
    _write_config(config_path, _valid_toml(tmp_path, api_port=6000))
    second = get_config(config_path)
    reloaded = reload_config(config_path)

    assert first is second
    assert second.api.port == 5219
    assert reloaded.api.port == 6000
    assert reloaded is get_config(config_path)


def test_reload_config_without_path_reuses_cached_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path, api_port=5219))
    get_config(config_path)

    _write_config(config_path, _valid_toml(tmp_path, api_port=6000))

    assert reload_config().api.port == 6000


def test_render_config_toml_preserves_dynamic_quoted_worker_keys(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(
        config_path,
        _valid_toml(tmp_path, worker_name="interactive.worker"),
    )
    config = load_config(config_path)

    rendered = render_config_toml(config)
    reparsed = LyraConfig.model_validate(config_module.tomllib.loads(rendered))

    assert '[workers."interactive.worker"]' in rendered
    assert sorted(reparsed.workers) == ["interactive.worker"]


def test_save_config_writes_loadable_toml_without_temp_file_leaks(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source" / "lyra.toml"
    target_path = tmp_path / "target" / "lyra.toml"
    _write_config(source_path, _valid_toml(tmp_path))
    config = load_config(source_path)

    save_config(config, target_path)

    assert load_config(target_path) == config
    assert "[plugins.metric_queues]" not in target_path.read_text(encoding="utf-8")
    assert not list(target_path.parent.glob(".lyra.toml.*.tmp"))


def test_ensure_runtime_directories_creates_non_secret_layout(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "lyra.toml"
    _write_config(config_path, _valid_toml(tmp_path))
    config = load_config(config_path)

    ensure_runtime_directories(config)

    expected_dirs = [
        tmp_path / "config",
        tmp_path / "cache" / "jobs" / "interactive",
        tmp_path / "plugins" / "catalog",
        tmp_path / "plugins" / "runners",
        tmp_path / "plugins" / "runners" / "interactive",
        tmp_path / "logs",
    ]
    assert all(path.is_dir() for path in expected_dirs)
    assert not (tmp_path / "secrets" / "generated_secret").exists()
