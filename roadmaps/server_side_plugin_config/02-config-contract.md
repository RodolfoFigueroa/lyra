# Config Contract

The server config file lives at:

```text
/lyra_data/config/lyra.toml
```

This file is operator-owned and server-side. It is not part of any plugin
repository. API and worker processes must parse the same config contract.

## Example

```toml
schema_version = 1

[api]
host = "0.0.0.0"
port = 5219

[redis]
url = "redis://redis:6379/0"

[database]
host = "postgres"
port = 5432
name = "lyra"
user = "lyra"
password_file = "/lyra_data/secrets/postgres_password"

[earth_engine]
project = "earth-engine-project"
service_account_file = "/lyra_data/secrets/service-account.json"

[admin]
api_key_file = "/lyra_data/secrets/admin_api_key"

[logging]
level = "INFO"
file = "/lyra_data/logs/lyra.log"

[job_store]
ttl_seconds = 600

[plugins]
repos = [
  "owner/plugin-a@main",
  "https://github.com/owner/plugin-b@v0.1.0",
  "file:///absolute/path/to/local-plugin"
]
catalog_dir = "/lyra_data/plugins/catalog"
runner_base_dir = "/lyra_data/plugins/runners"
default_queue = "interactive"
allowed_queues = ["interactive", "batch"]

[plugins.metric_queues]
walkability_score = "interactive"
regional_accessibility = "batch"

[workers.interactive]
queues = ["interactive"]
concurrency = 32
install_dir = "/lyra_data/plugins/runners/interactive"
temp_dir = "/lyra_data/cache/jobs/interactive"

[workers.batch]
queues = ["batch"]
concurrency = 8
install_dir = "/lyra_data/plugins/runners/batch"
temp_dir = "/lyra_data/cache/jobs/batch"
```

## Top-Level Fields

- `schema_version`: required integer. Initial value is `1`.

Unknown top-level sections or fields are invalid. Invalid config should fail at
startup before accepting API requests or worker jobs.

## API

`[api]` controls the FastAPI server process.

- `host`: bind host. Default: `"0.0.0.0"`.
- `port`: bind port. Default: `5219`.

## Redis

`[redis]` controls the Redis URL used by Celery and Lyra's job store clients.

- `url`: required Redis URL.

Celery broker and result backend must both use this URL unless a future config
version explicitly splits them.

## Database

`[database]` controls SQLAlchemy sync and async database engines.

- `host`: required database host.
- `port`: required integer database port.
- `name`: required database name.
- `user`: required database user.
- `password_file`: required file path containing the database password.

The password file content is read as UTF-8 text and trimmed of surrounding
whitespace.

## Earth Engine

`[earth_engine]` controls Google Earth Engine initialization.

- `project`: required Earth Engine project ID.
- `service_account_file`: required path to the service account JSON file.

The application must no longer assume `/app/service-account.json`.

## Admin

`[admin]` controls admin-route authentication.

- `api_key_file`: required file path containing the bearer token accepted by
  admin routes.

The API key file content is read as UTF-8 text and trimmed of surrounding
whitespace.

## Logging

`[logging]` controls Lyra application logging.

- `level`: log level string. Default: `"INFO"`.
- `file`: optional log file path. If omitted, log to stderr/stdout using the
  existing stream handler behavior.

When `file` is set, the parent directory must be created at startup.

## Job Store

`[job_store]` controls Redis-backed job status, result, and event retention.

- `ttl_seconds`: positive integer. Default: `600`.

## Plugins

`[plugins]` controls plugin repository discovery and persistent plugin data.

- `repos`: list of GitHub or `file://` plugin repository entries.
- `catalog_dir`: API catalog checkout directory.
- `runner_base_dir`: base directory for worker-specific plugin installs.
- `default_queue`: queue assigned to newly discovered metrics.
- `allowed_queues`: non-empty list of queue names accepted by metric
  assignments and worker definitions.

`repos` replaces `LYRA_PLUGIN_REPOS`. `catalog_dir` replaces
`LYRA_PLUGIN_CATALOG_DIR`. `runner_base_dir` replaces separate worker plugin
volume conventions.

## Metric Queues

`[plugins.metric_queues]` is a mapping from metric name to queue name.

The API is allowed to mutate this section during catalog refresh when it
discovers metrics that do not yet have assignments. Workers must never mutate
this section.

Every assigned queue must appear in `plugins.allowed_queues`.

## Workers

Each `[workers.<name>]` table defines one worker pool.

- `queues`: non-empty list of Celery queues consumed by this worker.
- `concurrency`: positive integer. Default: `1`.
- `install_dir`: plugin install directory for this worker. If omitted, use
  `{plugins.runner_base_dir}/{worker_name}`.
- `temp_dir`: base directory for per-job temporary files. If omitted, use
  `/lyra_data/cache/jobs/{worker_name}`.

Each queue in `workers.<name>.queues` must appear in `plugins.allowed_queues`.
Worker names are passed to worker processes as bootstrap command arguments.

