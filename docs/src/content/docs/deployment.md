---
title: Deployment
description: Run the API and workers from one server-owned TOML config and data volume.
---

Lyra separates deployment config from plugin operational state. API and worker
containers read `/lyra_data/config/lyra.toml` from a read-only file mount, read
the Earth Engine service account from a read-only file mount, receive
Postgres/admin settings from environment variables, and share one writable
`lyra_data` volume for Lyra-owned state and runtime files.

## API Containers

API containers:

- Read `/lyra_data/config/lyra.toml`.
- Create non-secret runtime directories under `/lyra_data`.
- Read plugin repositories and metric routing from
  `/lyra_data/state/plugins.toml`.
- Sync plugin manifests into `plugins.catalog_dir`.
- Validate job requests using compiled metric `request_schema` values.
- Assign missing metric queues in plugin state using `plugins.default_queue`.
- Dispatch the generic `lyra.run_metric` Celery task to the metric's
  server-assigned queue.

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

- Read plugin repositories and metric routing from
  `/lyra_data/state/plugins.toml`.
- Clone and install enabled plugin repositories at startup.
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

Compose also passes Postgres/admin settings from `.env` to each app container:

```yaml
environment:
  LYRA_POSTGRES_HOST: ${LYRA_POSTGRES_HOST}
  LYRA_POSTGRES_PORT: ${LYRA_POSTGRES_PORT}
  LYRA_POSTGRES_DB: ${LYRA_POSTGRES_DB}
  LYRA_POSTGRES_USER: ${LYRA_POSTGRES_USER}
  LYRA_POSTGRES_PASSWORD: ${LYRA_POSTGRES_PASSWORD}
  LYRA_ADMIN_API_KEY: ${LYRA_ADMIN_API_KEY}
```

Use `.env` for the host file locations and runtime env values:

```env
LYRA_CONFIG_FILE=./lyra_data/config/lyra.toml
LYRA_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
LYRA_POSTGRES_HOST=postgres
LYRA_POSTGRES_PORT=5432
LYRA_POSTGRES_DB=lyra
LYRA_POSTGRES_USER=lyra
LYRA_POSTGRES_PASSWORD=change-me
LYRA_ADMIN_API_KEY=change-me
```

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

1. Add or update plugin repositories with `/admin/plugin-repos`.
2. Refresh the API manifest catalog with
   `POST /admin/plugin-catalog/refresh`.
3. Review or adjust metric routing with `/admin/plugin-routing`.
4. Restart warm worker pools so they reinstall plugin code and rebuild their
   runner registries.

Workers do not hot-reload plugin code in-process.

Kubernetes manifests are not part of the checked-in deployment shape.
