from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = [
    ROOT / "docker" / "docker-compose.yml",
    ROOT / "docker" / "docker-compose-dev.yml",
]
APP_FILE_MOUNTS = [
    "${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml:ro",
    "${LYRA_SERVICE_ACCOUNT_FILE}:/lyra_data/secrets/service-account.json:ro",
]
APP_ENVIRONMENT_ENTRIES = [
    "LYRA_POSTGRES_HOST: ${LYRA_POSTGRES_HOST}",
    "LYRA_POSTGRES_PORT: ${LYRA_POSTGRES_PORT}",
    "LYRA_POSTGRES_DB: ${LYRA_POSTGRES_DB}",
    "LYRA_POSTGRES_USER: ${LYRA_POSTGRES_USER}",
    "LYRA_POSTGRES_PASSWORD: ${LYRA_POSTGRES_PASSWORD}",
    "LYRA_ADMIN_API_KEY: ${LYRA_ADMIN_API_KEY}",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compose_uses_single_lyra_data_volume() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert "lyra_data:/lyra_data" in contents
        assert contents.count("volumes: *lyra-app-volumes") == 3
        assert "lyra_data:" in contents
        assert "name: lyra_data" in contents
        assert "lyra_plugin_catalog" not in contents
        assert "lyra_plugins_" not in contents
        assert "/lyra_cache" not in contents
        assert "/app/service-account.json" not in contents


def test_compose_mounts_config_and_service_account_as_read_only_files() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        for mount in APP_FILE_MOUNTS:
            assert mount in contents
        assert "/lyra_data/state/plugins.toml:" not in contents
        assert "${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml" in contents
        assert "postgres_password" not in contents
        assert "admin_api_key" not in contents


def test_compose_passes_env_backed_runtime_settings() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert contents.count("environment: *lyra-app-environment") == 3
        for entry in APP_ENVIRONMENT_ENTRIES:
            assert entry in contents


def test_compose_passes_worker_names_instead_of_queue_env() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert "python -m lyra_app.worker_launcher interactive" in contents
        assert "python -m lyra_app.worker_launcher batch" in contents
        assert "LYRA_RUNNER_QUEUES" not in contents
        assert "CELERY_BROKER_URL" not in contents
        assert "LYRA_PLUGIN_REPOS" not in contents
        assert "env_file:" not in contents


def test_compose_waits_for_api_catalog_initialization() -> None:
    for compose_file in COMPOSE_FILES:
        contents = _read(compose_file)

        assert "healthcheck:" in contents
        assert "condition: service_healthy" in contents
        assert "urllib.request.urlopen" in contents


def test_env_example_defines_host_mount_paths_and_runtime_env() -> None:
    contents = _read(ROOT / ".env.example")

    assert "LYRA_CONFIG_FILE=./lyra_data/config/lyra.toml" in contents
    assert "LYRA_SERVICE_ACCOUNT_FILE=./secrets/service-account.json" in contents
    assert "LYRA_POSTGRES_HOST=postgres" in contents
    assert "LYRA_POSTGRES_PORT=5432" in contents
    assert "LYRA_POSTGRES_DB=lyra" in contents
    assert "LYRA_POSTGRES_USER=lyra" in contents
    assert "LYRA_POSTGRES_PASSWORD=change-me" in contents
    assert "LYRA_ADMIN_API_KEY=change-me" in contents
    assert "LYRA_PLUGIN_REPOS" not in contents
    assert "EARTHENGINE_PROJECT" not in contents


def test_dockerfile_declares_lyra_data_volume_only() -> None:
    contents = _read(ROOT / "Dockerfile")

    assert "VOLUME /lyra_data" in contents
    assert "/lyra_data/secrets" in contents
    assert "/lyra_data/state" in contents
    assert "VOLUME /lyra_plugin_catalog" not in contents
    assert "VOLUME /lyra_plugins" not in contents
    assert "LYRA_PLUGIN_REPOS" not in contents
