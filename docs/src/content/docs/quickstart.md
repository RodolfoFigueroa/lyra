---
title: Quickstart
description: Configure and start Lyra, then submit one authenticated job.
---

This path starts the API, Redis, and two worker pools with Docker Compose. Lyra
does not bundle spatial data or credentials.

## Prerequisites

Provide:

- Docker with Compose;
- a reachable PostGIS database containing Lyra's census and metropolitan-zone
  tables;
- a Google Earth Engine project and readable service-account JSON;
- one random agent key and a different random admin key.

## Configure

From the repository root:

```bash
mkdir -p lyra_data/config secrets
cp config.example.toml lyra_data/config/lyra.toml
cp .env.example .env
```

Edit `lyra_data/config/lyra.toml` and set the Earth Engine project and `[database]` host, port, name, and user. Its checked-in
defaults target the development Compose Redis service and use
`http://localhost:5219` as the public API URL.

Put the service-account JSON at `secrets/service-account.json`. Edit `.env` with
`LYRA_POSTGRES_PASSWORD` and two different API secrets:

```text
LYRA_AGENT_API_KEY=replace-with-a-random-agent-secret
LYRA_ADMIN_API_KEY=replace-with-a-different-admin-secret
```

## Start

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

Wait for readiness:

```bash
curl http://localhost:5219/live
curl http://localhost:5219/ready
```

`/live` only proves the API process is running. `/ready` returns `200` only when
Redis and PostGIS are reachable and the plugin catalog initialized successfully.

## Configure plugins

Declare sources in the TOML before startup:

```toml
[[plugins.repos]]
id = "example"
source = "owner/plugin-repository"
ref = "main"
```

For local development, use `source = "dir:///absolute/path/to/plugin"` and make
that path visible inside the API container. The API captures it for workers.
Git sources accept branches, tags, or commits in the separate `ref` field.

Validate with `uv run lyra-admin config validate lyra_data/config/lyra.toml`.
After editing a running deployment, follow the [drain and restart procedure](../operate/deployment/#updates).
No admin API registration is needed.

## Submit a job

Discover the live contract instead of copying an arbitrary metric payload:

```bash
curl http://localhost:5219/metrics
curl http://localhost:5219/metrics/METRIC_NAME
```

For the configured example plugin, submit `smoke_table_metric` with nested
parameters. For another metric, use its advertised schema. A declared parameter
model requires `parameters` even when its value is `{}`; omit that property for
parameterless metrics. Keep the idempotency key when retrying an uncertain request:

```bash
curl -X POST http://localhost:5219/jobs \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"metric":"smoke_table_metric","input":{"location":{"data_type":"met_zone_code","value":"09.01"},"parameters":{"value":7}},"idempotency_key":"quickstart-1"}'
```

Use the returned `job_id` to poll status and read the terminal descriptor:

```bash
curl http://localhost:5219/jobs/JOB_ID \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"

curl http://localhost:5219/jobs/JOB_ID/result/descriptor \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"
```

Results expire. Download table JSONL or file output before the descriptor's
lifetime reaches zero.
