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

Edit `lyra_data/config/lyra.toml` and set the Earth Engine project. Its checked-in
defaults target the development Compose Redis service and use
`http://localhost:5219` as the public API URL.

Put the service-account JSON at `secrets/service-account.json`. Edit `.env` with
PostGIS connection values and two different secrets:

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
Redis and PostGIS are reachable.

## Add a plugin

For a local checkout, make the example visible inside every app container or
push it to a reachable Git repository. Register the source with the admin key:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"source":"owner/plugin-repository@main"}'
```

Refresh manifests, then restart workers when recommended:

```bash
curl -X POST http://localhost:5219/admin/plugin-catalog/refresh \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"

curl -X POST 'http://localhost:5219/admin/workers/restart?timeout=30' \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}"
```

## Submit a job

Discover the live contract instead of copying an arbitrary metric payload:

```bash
curl http://localhost:5219/metrics
curl http://localhost:5219/metrics/METRIC_NAME
```

Replace the placeholders with one returned metric and its required spatial
field. Keep the idempotency key when retrying an uncertain request:

```bash
curl -X POST http://localhost:5219/jobs \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"metric":"METRIC_NAME","input":{"SPATIAL_FIELD":{"data_type":"met_zone_code","value":"09.01"}},"idempotency_key":"quickstart-1"}'
```

Use the returned `job_id` to stream events and read the terminal descriptor:

```bash
curl -N http://localhost:5219/jobs/JOB_ID/events \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"

curl http://localhost:5219/jobs/JOB_ID/result/descriptor \
  -H "Authorization: Bearer ${LYRA_AGENT_API_KEY}"
```

Results expire. Download table JSONL or file output before the descriptor's
lifetime reaches zero.
