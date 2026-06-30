# Runtime Loading And Validation

Lyra must have one central config loader. Runtime modules should consume typed
config objects from that loader instead of reading environment variables
directly.

## Loader

Add a config module in the application package. The module should:

- Read `/lyra_data/config/lyra.toml` by default.
- Parse TOML with Python 3.11 `tomllib`.
- Validate parsed data into typed Python models.
- Cache the loaded config for normal runtime reads.
- Provide an explicit reload function for tests and admin operations that need
  to re-read config after metric queue assignments are persisted.

The config path may be overrideable for tests and local development, but app
code should treat `/lyra_data/config/lyra.toml` as the production default.

## TOML Writing

`tomllib` only reads TOML. Metric queue auto-assignment requires writing back to
`[plugins.metric_queues]`.

Use a TOML writing library when implementing persistence. `tomlkit` is the
preferred option if preserving comments and table order matters. If the
implementation chooses a simpler writer, it must still produce stable,
human-readable TOML and must not drop unrelated config fields.

Writes must be atomic:

- Write the new config to a temporary file in `/lyra_data/config`.
- Flush and close the temporary file.
- Replace `/lyra_data/config/lyra.toml` with an atomic rename.

## Validation Rules

Startup validation must fail fast for:

- Missing config file.
- Invalid TOML syntax.
- Unknown sections or fields.
- Missing required sections.
- Invalid types.
- Empty required strings.
- Non-positive ports, TTLs, and worker concurrency values.
- Secret reference files that do not exist or are empty.
- Metric queue assignments not listed in `plugins.allowed_queues`.
- Worker queue entries not listed in `plugins.allowed_queues`.
- Duplicate plugin repository entries after trimming whitespace.

Defaults should be applied only where the config contract defines them. Do not
silently invent defaults for required deployment settings such as Redis URL,
database connection fields, Earth Engine project, admin key file, or plugin
paths.

## Secret References

Config models should expose resolved secret values only where needed by runtime
code. The TOML file stores paths:

- `database.password_file`
- `earth_engine.service_account_file`
- `admin.api_key_file`

File contents should be read as UTF-8 text and trimmed when the secret is a
scalar value. The service account JSON should be passed by path to the Google
credentials loader.

## Runtime Consumption

Replace direct environment-variable reads with typed config access:

- Redis clients and Celery app use `config.redis.url`.
- Database engines use `config.database`.
- Earth Engine initialization uses `config.earth_engine`.
- Admin auth reads `config.admin` and the referenced API key file.
- Logging setup uses `config.logging`.
- Job store TTL uses `config.job_store.ttl_seconds`.
- Plugin sync uses `config.plugins.repos`, `catalog_dir`, and worker
  `install_dir`.
- API job dispatch uses resolved metric queue assignments.
- Worker startup uses the selected `workers.<name>` table.
- API server startup uses `config.api.host` and `config.api.port`.

## Bootstrap Inputs

Only bootstrap process selection may live outside TOML. In practice:

- API process: no extra app setting is required.
- Worker process: pass the worker name as a command argument, for example
  `interactive` or `batch`.

The worker name selects `[workers.<name>]`. If the name is missing or unknown,
the worker process must fail at startup.

