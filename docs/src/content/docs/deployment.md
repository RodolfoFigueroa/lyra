---
title: Deployment
description: Run the API and workers from one server-owned TOML config and data volume.
---

Lyra separates deployment config from plugin operational state. API and worker
containers read `/lyra_data/config/lyra.toml` from a read-only file mount, read
secret files from read-only file mounts, and share one writable `lyra_data`
volume for Lyra-owned state and runtime files.

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
mounts the config file and each secret file as read-only bind mounts:

```yaml
volumes:
  - lyra_data:/lyra_data
  - ${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml:ro
  - ${LYRA_POSTGRES_PASSWORD_FILE}:/lyra_data/secrets/postgres_password:ro
  - ${LYRA_ADMIN_API_KEY_FILE}:/lyra_data/secrets/admin_api_key:ro
  - ${LYRA_SERVICE_ACCOUNT_FILE}:/lyra_data/secrets/service-account.json:ro
```

Use `.env` only for those host file locations. These variables do not configure
Lyra inside the container:

```env
LYRA_CONFIG_FILE=./lyra_data/config/lyra.toml
LYRA_POSTGRES_PASSWORD_FILE=./secrets/postgres_password
LYRA_ADMIN_API_KEY_FILE=./secrets/admin_api_key
LYRA_SERVICE_ACCOUNT_FILE=./secrets/service-account.json
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
  secrets/postgres_password     # read-only file mount
  secrets/admin_api_key         # read-only file mount
  secrets/service-account.json  # read-only file mount
  state/plugins.toml            # Lyra-owned writable state
  cache/jobs/                   # Lyra-created job temp data
  plugins/catalog/              # Lyra-created API catalog checkouts
  plugins/runners/              # Lyra-created worker installs
  logs/                         # optional Lyra-created logs
```

Secret files are deployment-owned. Lyra references them by path and does not
generate placeholder secrets. The default paths are
`/lyra_data/secrets/postgres_password`, `/lyra_data/secrets/admin_api_key`, and
`/lyra_data/secrets/service-account.json`; mount deployment secrets to those
paths, or override the secret path fields in TOML.

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
