from pathlib import Path
from unittest.mock import Mock

import pytest
from rq.serializers import JSONSerializer

from lyra_app import worker_launcher
from tests.config_helpers import load_test_config


def test_launcher_initializes_and_runs_native_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    config.workers["interactive"].concurrency = 5
    pool = Mock()
    pool_factory = Mock(return_value=pool)
    calls = []
    for name in [
        "ensure_runtime_directories",
        "configure_logging",
        "configure_redis",
        "probe_worker_database",
        "initialize_earth_engine",
    ]:
        monkeypatch.setattr(
            worker_launcher, name, lambda _config, name=name: calls.append(name)
        )
    refresh = Mock()
    monkeypatch.setattr(worker_launcher, "refresh_runner_registry", refresh)
    monkeypatch.setattr(worker_launcher, "WorkerPool", pool_factory)
    worker_launcher.launch_worker("interactive", config=config)
    assert calls == [
        "ensure_runtime_directories",
        "configure_logging",
        "configure_redis",
        "probe_worker_database",
        "initialize_earth_engine",
    ]
    refresh.assert_called_once_with("interactive", config=config)
    assert pool_factory.call_args.kwargs["num_workers"] == 5
    assert pool_factory.call_args.kwargs["serializer"] is JSONSerializer
    assert pool_factory.call_args.kwargs["worker_class"] is worker_launcher.JsonWorker
    pool.start.assert_called_once_with(burst=False, logging_level="INFO")


def test_unknown_pool_exits_before_initialization(tmp_path: Path) -> None:
    load_test_config(tmp_path)
    with pytest.raises(SystemExit) as error:
        worker_launcher.main(["missing"])
    assert error.value.code == 2


def test_probe_failure_stops_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_test_config(tmp_path)
    for worker in config.workers.values():
        worker.temp_dir = tmp_path / "scratch"
    probe = Mock(side_effect=RuntimeError("database unavailable"))
    initialize = Mock()
    monkeypatch.setattr(worker_launcher, "probe_worker_database", probe)
    monkeypatch.setattr(worker_launcher, "initialize_earth_engine", initialize)
    with pytest.raises(RuntimeError, match="database unavailable"):
        worker_launcher.launch_worker("interactive", config=config)
    initialize.assert_not_called()
