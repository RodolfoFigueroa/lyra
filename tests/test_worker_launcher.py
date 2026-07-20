from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lyra_app import worker_launcher
from lyra_app.config import LyraConfig, WorkerConfig, clear_config_cache
from tests.config_helpers import load_test_config, plugin_state_store

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_config_cache() -> Iterator[None]:
    clear_config_cache()
    yield
    clear_config_cache()


def _local_worker_dirs(config: LyraConfig, base: Path) -> LyraConfig:
    workers: dict[str, WorkerConfig] = {}
    for worker_name, worker in config.workers.items():
        workers[worker_name] = worker.model_copy(
            update={
                "install_dir": base / "plugins" / "runners" / worker_name,
                "temp_dir": base / "cache" / "jobs" / worker_name,
            },
        )
    return config.model_copy(update={"workers": workers})


def test_build_celery_worker_args_uses_toml_worker_settings(tmp_path: Path) -> None:
    config = _local_worker_dirs(load_test_config(tmp_path), tmp_path)
    interactive = config.get_worker("interactive").model_copy(
        update={"queues": ["interactive", "priority-lane"], "concurrency": 7},
    )
    config = config.model_copy(
        update={"workers": {**config.workers, "interactive": interactive}},
    )

    assert worker_launcher.build_celery_worker_args(config, "interactive") == [
        "worker",
        "--hostname",
        "interactive@%h",
        "--loglevel",
        "INFO",
        "--concurrency",
        "7",
        "-Q",
        "interactive,priority-lane",
    ]


def test_main_reports_unknown_worker_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    load_test_config(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        worker_launcher.main(["missing"])

    assert exc_info.value.code == 2
    assert "unknown worker config: missing" in capsys.readouterr().err


def test_launch_worker_prepares_dirs_refreshes_registry_and_starts_celery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _local_worker_dirs(load_test_config(tmp_path), tmp_path)
    launched: list[list[str]] = []
    refreshed: list[tuple[str, LyraConfig]] = []
    earth_engine_configs: list[LyraConfig] = []

    class FakeCelery:
        def __init__(self) -> None:
            self.conf: dict[str, str] = {}

        def worker_main(self, args: list[str]) -> None:
            launched.append(args)

    fake_celery = FakeCelery()

    def configure_celery(config: LyraConfig) -> None:
        fake_celery.conf.update(
            broker_url=config.redis.url,
            result_backend=config.redis.url,
        )

    def refresh_runner_registry(
        worker_name: str,
        *,
        config: LyraConfig,
        store: object,
    ) -> None:
        assert store is state_store
        refreshed.append((worker_name, config))

    monkeypatch.setitem(
        sys.modules,
        "lyra_app.celery_app",
        SimpleNamespace(celery_app=fake_celery, configure_celery=configure_celery),
    )
    monkeypatch.setitem(
        sys.modules,
        "lyra_app.worker",
        SimpleNamespace(refresh_runner_registry=refresh_runner_registry),
    )
    monkeypatch.setattr(
        worker_launcher,
        "initialize_earth_engine",
        earth_engine_configs.append,
    )

    state_store = plugin_state_store(tmp_path, config)
    worker_launcher.launch_worker("interactive", config=config, store=state_store)

    assert fake_celery.conf == {
        "broker_url": "redis://redis:6379/0",
        "result_backend": "redis://redis:6379/0",
    }
    assert earth_engine_configs == [config]
    assert refreshed == [("interactive", config)]
    assert launched == [worker_launcher.build_celery_worker_args(config, "interactive")]
    assert (tmp_path / "plugins" / "catalog").is_dir()
    assert (tmp_path / "cache" / "jobs" / "interactive").is_dir()
    assert not (tmp_path / "secrets" / "generated_secret").exists()


def test_launch_worker_rejects_missing_plugin_state(tmp_path: Path) -> None:
    config = _local_worker_dirs(load_test_config(tmp_path), tmp_path)
    state_store = plugin_state_store(tmp_path, config)
    state_store.path.unlink()

    with pytest.raises(RuntimeError, match="Plugin state is not initialized"):
        worker_launcher.launch_worker(
            "interactive",
            config=config,
            store=state_store,
        )
