---
title: TUI
description: Run the Lyra terminal operator console against a live API.
---

The Lyra TUI is a first-party operator console for a running Lyra API. It shows
health, jobs, workers, queues, plugin catalog state, plugin repositories, and
metric routing in one terminal. It connects over HTTP(S) through the same public
and admin API routes used by other clients; it does not start Redis, the API, or
workers itself.

## Start Lyra First

Install the workspace before running the TUI:

```bash
uv sync
```

For a full local stack, start the development Compose file from the repository
root:

```bash
docker compose -f docker/docker-compose-dev.yml up --build
```

You can also run the API and workers directly. Start Redis, make sure
`/lyra_data/config/lyra.toml` points at that Redis instance, export the
Postgres and admin environment variables, then start a worker and the API:

```bash
uv run python -m lyra_app.worker_launcher interactive
uv run python -m lyra_app.main
```

## Run The TUI

The default local development API is `localhost:5219` over HTTP:

```bash
uv run lyra-tui --host localhost:5219 --no-secure
```

Pass the admin key with the environment variable used by the server:

```bash
LYRA_ADMIN_API_KEY=... uv run lyra-tui --host localhost:5219 --no-secure
```

Or pass it explicitly:

```bash
uv run lyra-tui --host localhost:5219 --no-secure --admin-api-key ...
```

Use `--secure` for HTTPS endpoints. The `--host` value is only the host and
optional port, without `http://` or `https://`; choose the scheme with
`--secure` or `--no-secure`.

## Auth And Actions

`GET /health` is public, so the TUI can still show API and Redis health without
an admin key. All admin views and all mutating actions require the same Bearer
token as Lyra's `/admin/*` routes. The TUI reads that token from
`LYRA_ADMIN_API_KEY` unless `--admin-api-key` is provided.

The MVP console can:

- cancel active jobs
- restart worker pools
- add, enable, disable, delete, and sync plugin repositories
- refresh the plugin catalog
- assign and delete metric queue routes

Destructive or disruptive actions ask for confirmation before they call the API.

## Options

| Option | Purpose |
| --- | --- |
| `--host HOST[:PORT]` | Lyra API host and optional port. Defaults to `localhost:5219`. |
| `--secure` | Connect with HTTPS. |
| `--no-secure` | Connect with HTTP. This is the local development default. |
| `--admin-api-key TOKEN` | Admin Bearer token. Defaults to `LYRA_ADMIN_API_KEY`. |
| `--timeout SECONDS` | Per-request HTTP timeout. |
| `--refresh-interval SECONDS` | Automatic refresh interval. |

## Troubleshooting

If the API is offline, the TUI reports the health request failure. Start the API
or check `--host`, `--secure`, and `--no-secure`.

If the admin key is missing, the TUI shows public health only and labels admin
data as locked. Export `LYRA_ADMIN_API_KEY` or pass `--admin-api-key`.

If Redis is unavailable, `/health` reports Redis as unavailable and job, queue,
and worker data may be stale or missing. Start Redis and confirm the server's
`[redis].url` setting.

If worker inspect is unavailable, workers may appear as `unknown`. This usually
means workers are offline, restarting, or not responding to Celery inspect.

If queue pending depth is unknown, the API returns an explicit unknown marker
instead of guessing. Queue assignments and consumers can still be useful even
when exact depth is unavailable.
