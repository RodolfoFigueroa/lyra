---
title: Administrative CLI
description: Inspect and manage an existing Lyra server with lyra-admin.
---

`lyra-admin` ships in the `lyra-api` Python distribution. In this repository,
run `uv sync`, then `uv run lyra-admin`. Outside the repository, install a
released `lyra-api` wheel and its dependencies with uv. Component wheels are
currently release build artifacts; see [Contributing](https://github.com/RodolfoFigueroa/lyra/blob/main/CONTRIBUTING.md)
for release details.

The CLI connects to an existing server. Each command prints its result and
exits. It does not start the API, databases, or workers.

## Connection and credentials

```bash
uv run lyra-admin --host localhost:5219 --no-secure health
# Set LYRA_ADMIN_API_KEY in your environment before protected commands.
uv run lyra-admin --host lyra.example.com --secure status
uv run lyra-admin --json jobs list --queue interactive --status running --limit 20
```

Put global options before the command. `--host` accepts
`HOST[:PORT][/BASE_PATH]` without a URL scheme. Defaults are `localhost:5219`,
HTTP, and a 30-second HTTP request timeout. Select HTTPS with `--secure`, and
set the request timeout with `--timeout SECONDS`.

`--admin-api-key TOKEN` overrides `LYRA_ADMIN_API_KEY`. Health checks are public;
remote inspection commands require the admin key. Offline
`config validate PATH` requires no credentials. Agent credentials and consumer job
continuous observation is outside this CLI's interface.

## Inspection

| Command | Result |
| --- | --- |
| `health` | Redis, PostgreSQL, and catalog readiness; exits nonzero if unhealthy. |
| `health --live` | API process liveness. |
| `status` | Administrative service summary. |
| `config-summary` | Effective configuration with secrets omitted. |
| `jobs list --queue NAME --status STATE` | Native queue/status page, default limit 50. |
| `jobs get ID` | Status, queue, worker ID, and administrator-only diagnostics. |
| `workers list` | Worker state and observation metadata. |
| `workers get NAME` | One native worker process and heartbeat. |
| `queues list` | Routing coverage, worker coverage, and queue depth. |
| `plugins list` | Configured distributions, installed versions, and enabled states. |
| `catalog show` | Catalog fingerprint, metric names, installed plugins, and routing. |
| `routing list` | Effective routes, per-plugin overrides, disabled plugins, and queues. |

Job listing requires `--queue NAME` and `--status queued|running|succeeded|failed`.
Use `--offset N` and `--limit N` (1–200) for pagination. Queued pages use FIFO;
completed/failed pages use descending registry order. Transitions and expired
records can shorten pages.

Worker results show heartbeat freshness and keep configured pools separate from
native processes. Redis unavailability is an explicit error.

## Offline configuration validation

```bash
uv run lyra-admin config validate ./lyra.toml
uv run lyra-admin --json config validate ./lyra.toml
```

This command only reads the specified TOML and validates its schema and internal
relationships. It requires no server, secrets, network, or writable directories.
It does not verify plugin manifests or service availability. Successful JSON is
`{"valid": true, "schema_version": 4}`. Invalid TOML/schema exits `2`; file I/O
failures exit `1`.

Edit the file directly and restart the API and all workers to apply settings.
See [Deployment](../deployment/) for startup, draining, and replication.

## JSON and exit codes

`--json` writes one complete API response object to stdout, followed by a newline.
It includes all response-model fields, including nulls and observation metadata.
There is no CLI envelope. Errors go to stderr as a JSON object:

```json
{"error": {"kind": "usage", "message": "Set LYRA_ADMIN_API_KEY or supply --admin-api-key."}}
```

An unhealthy readiness response can produce
both a response on stdout and an error on stderr. Check the exit code before
assuming success. Help remains plain text, including when `--json` is present.

| Exit code | Meaning |
| --- | --- |
| `0` | Successful command or help. |
| `1` | Request/response failure, unsuccessful health check, or operation failure. |
| `2` | Invalid arguments, configuration, or missing credentials. |
| `130` | Interrupted. |

Error kinds are `usage`, `request`, `operation`, and `interrupted`.
Response JSON follows the installed API client's models. Scripts should use field
names rather than parse human-readable tables. The CLI does not export or apply
configuration files.

## Command coverage

The CLI provides health, status, loaded configuration, jobs, workers, queues,
plugins, catalog, and routing inspection. It also supports offline configuration validation. Configuration writes, catalog refresh,
and worker restart commands and HTTP APIs are removed.

See the generated [CLI reference](../../reference/generated/cli/) for root,
group, and individual command help, or use `lyra-admin COMMAND --help`.
