from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = [
    ROOT / "docker" / "docker-compose.yml",
    ROOT / "docker" / "docker-compose-dev.yml",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compose_uses_single_lyra_data_volume() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert "lyra_data:/lyra_data" in contents
        assert "lyra_data:" in contents
        assert "name: lyra_data" in contents
        assert "lyra_plugin_catalog" not in contents
        assert "lyra_plugins_" not in contents
        assert "/lyra_cache" not in contents
        assert "/app/service-account.json" not in contents


def test_compose_passes_worker_names_instead_of_queue_env() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert "python -m lyra_app.worker_launcher interactive" in contents
        assert "python -m lyra_app.worker_launcher batch" in contents
        assert "LYRA_RUNNER_QUEUES" not in contents
        assert "CELERY_BROKER_URL" not in contents
        assert "LYRA_PLUGIN_REPOS" not in contents
        assert "LYRA_ADMIN_API_KEY" not in contents
        assert "env_file:" not in contents


def test_dockerfile_declares_lyra_data_volume_only() -> None:
    contents = _read(ROOT / "Dockerfile")

    assert "VOLUME /lyra_data" in contents
    assert "VOLUME /lyra_plugin_catalog" not in contents
    assert "VOLUME /lyra_plugins" not in contents
    assert "LYRA_PLUGIN_REPOS" not in contents
