---
title: Deployment
description: Run the API and workers from one server-owned TOML config and data volume.
---

Lyra separates deployment config from plugin operational state. API and worker
containers read `/lyra_data/config/lyra.toml` from a read-only file mount, read
the Earth Engine service account from a read-only file mount, receive
Postgres plus separate agent/admin credentials from environment variables, and share one writable
`lyra_data` volume for Lyra-owned state and runtime files.

## API Containers

API containers:

- Read `/lyra_data/config/lyra.toml`.
- Create non-secret runtime directories under `/lyra_data`.
- Read plugin sources and metric routing from
  `/lyra_data/state/plugins.toml`.
- Sync plugin manifests into `plugins.catalog_dir`.
- Validate job requests using compiled metric `request_schema` values.
- Assign missing metric queues in plugin state using `plugins.default_queue`.
- Dispatch the generic `lyra.run_metric` Celery task to the metric's
  server-assigned queue.
- Mount the official Python MCP SDK's Streamable HTTP transport when enabled.
- Use `api.public_base_url` for absolute authenticated JSONL handoffs.

The API catalog does not import plugin Python code.

## Worker Containers

Worker containers start with a worker name:

```bash
python -m lyra_app.worker_launcher interactive
```

The launcher reads `[workers.interactive]` for queue membership, concurrency,
install directory, and temp directory. It then starts Celery with the matching
`-Q` and concurrency values.

Workers:

- Read plugin sources and metric routing from
  `/lyra_data/state/plugins.toml`.
- Sync and install enabled plugin sources at startup.
- Read schema v3 manifests from installed plugins.
- Import only metrics whose server-assigned queue belongs to the worker.
- Consume the same queues through Celery.

## Docker Compose

The Compose examples define one named volume:

```yaml
volumes:
  lyra_data:
    name: lyra_data
```

Every Lyra app container mounts it at `/lyra_data`. Each app container also
mounts the config file and Earth Engine service account as read-only bind
mounts:

```yaml
volumes:
  - lyra_data:/lyra_data
  - ${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml:ro
  - ${LYRA_SERVICE_ACCOUNT_FILE}:/lyra_data/secrets/service-account.json:ro
```

Compose also passes Postgres plus agent/admin settings from `.env`:

```yaml
environment:
  LYRA_POSTGRES_HOST: ${LYRA_POSTGRES_HOST}
  LYRA_POSTGRES_PORT: ${LYRA_POSTGRES_PORT}
  LYRA_POSTGRES_DB: ${LYRA_POSTGRES_DB}
  LYRA_POSTGRES_USER: ${LYRA_POSTGRES_USER}
  LYRA_POSTGRES_PASSWORD: ${LYRA_POSTGRES_PASSWORD}
  LYRA_AGENT_API_KEY: ${LYRA_AGENT_API_KEY}
  LYRA_ADMIN_API_KEY: ${LYRA_ADMIN_API_KEY}
```

Use `.env` for the host file locations and runtime env values:

```text
LYRA_CONFIG_FILE=./lyra_data/config/lyra.toml
LYRA_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
LYRA_POSTGRES_HOST=postgres
LYRA_POSTGRES_PORT=5432
LYRA_POSTGRES_DB=lyra
LYRA_POSTGRES_USER=lyra
LYRA_POSTGRES_PASSWORD=change-me
LYRA_AGENT_API_KEY=replace-with-agent-secret
LYRA_ADMIN_API_KEY=replace-with-admin-secret
```

Generate the credentials independently. The agent key is for MCP and every
`/jobs` route; the admin key is only for `/admin` and must not be distributed to
agents.

## Agent Bridge Configuration

```toml
[api]
host = "0.0.0.0"
port = 5219
public_base_url = "https://lyra.example.com"

[mcp]
enabled = true
mount_path = "/mcp"

[job_store]
ttl_seconds = 600

[agent_submission_limit]
limit = 10
window_seconds = 60
```

`public_base_url` is an externally reachable base URL, not a bind address. It
may contain a reverse-proxy path prefix. Use HTTPS in production; loopback HTTP
is accepted for local development. Do not include credentials, a query, or a
fragment. Preserve the `Authorization` header at the proxy.

The default fixed window accepts 10 new REST/MCP submissions every 60 seconds.
Tune both positive integers to capacity. Replays of equivalent idempotent
requests consume no capacity. REST returns `429` plus `Retry-After`; MCP returns
`rate_limited` plus `retry_after_seconds`. Clients wait and retry with the same
key.

The job-store TTL covers status, events, provenance, results, and associated
idempotency records. Descriptors expose remaining lifetime. Downstream systems
must download needed results before expiry; Lyra is not durable result storage.

`/lyra_data/state/plugins.toml` is not mounted from the host. Lyra creates and
writes it inside the named volume.

The checked-in examples include two worker pools:

- `interactive`
- `batch`

To add another worker pool, add a `[workers.<name>]` table in TOML and another
service that runs `python -m lyra_app.worker_launcher <name>`.

## Filesystem Layout

Use this tree inside the volume:

```text
/lyra_data/
  config/lyra.toml              # read-only file mount
  secrets/service-account.json  # read-only file mount
  state/plugins.toml            # Lyra-owned writable state
  cache/jobs/                   # Lyra-created job temp data
  plugins/catalog/              # Lyra-created API catalog checkouts
  plugins/runners/              # Lyra-created worker installs
  logs/                         # optional Lyra-created logs
```

The service-account file is deployment-owned. Lyra references it by path and
does not generate placeholder secrets. The default path is
`/lyra_data/secrets/service-account.json`; mount the deployment secret there,
or override `earth_engine.service_account_file` in TOML.

## Plugin Updates

Plugin updates are explicit:

1. Add or update plugin sources with `/admin/plugin-repos`.
2. Refresh the API manifest catalog with
   `POST /admin/plugin-catalog/refresh`.
3. Review or adjust metric routing with `/admin/plugin-routing`.
4. Restart warm worker pools so they reinstall plugin code and rebuild their
   runner registries with `POST /admin/workers/restart`.

Workers do not hot-reload plugin code in-process.

## Observability

Use `GET /health` for load balancers and local liveness checks. Use admin
observability routes for operator dashboards:

- `GET /admin/status`
- `GET /admin/config-summary`
- `GET /admin/catalog`
- `GET /admin/workers`
- `GET /admin/queues`

Celery worker inspection can be unavailable during deploys or restarts. In that
case Lyra returns `unknown` worker state and unknown queue depths instead of
failing the API.

Production deployments should normally use GitHub or `file://` local git
sources. Development `dir://` sources are supported for local mock plugins and
executor testing; they copy uncommitted directory contents on refresh. If a
deployment uses `dir://`, mount the source directory into every API and worker
container at the same absolute path, for example `/plugins/mock-plugin`, and
register `dir:///plugins/mock-plugin`.

Kubernetes manifests are not part of the checked-in deployment shape.
