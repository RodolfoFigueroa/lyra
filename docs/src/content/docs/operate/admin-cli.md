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
uv run lyra-admin --json jobs list --status running --limit 20
```

Put global options before the command. `--host` accepts
`HOST[:PORT][/BASE_PATH]` without a URL scheme. Defaults are `localhost:5219`,
HTTP, and a 30-second HTTP request timeout. Select HTTPS with `--secure`, and
set the request timeout with `--timeout SECONDS`.

`--admin-api-key TOKEN` overrides `LYRA_ADMIN_API_KEY`. Health checks are public;
remote inspection and cancellation commands require the admin key. Offline
`config validate PATH` requires no credentials. Agent credentials and consumer job
continuous observation is outside this CLI's interface.

## Inspection

| Command | Result |
| --- | --- |
| `health` | Redis, PostgreSQL, and catalog readiness; exits nonzero if unhealthy. |
| `health --live` | API process liveness. |
| `status` | Administrative service summary. |
| `config-summary` | Effective configuration with secrets omitted. |
| `jobs list` | Up to 50 recent retained jobs, newest first. |
| `workers list` | Worker state and observation metadata. |
| `workers get NAME` | One worker's tasks, statistics, and observation metadata. |
| `queues list` | Routing coverage, worker coverage, and queue depth. |
| `repos list` | Repository identities, sources, requested/resolved refs, and enabled states. |
| `catalog show` | Catalog fingerprint, metric names, plugin sources, and routing. |
| `routing list` | Effective routes, per-repository overrides, disabled repositories, and queues. |

Filter jobs with `--status queued|running|succeeded|failed|cancelled`,
`--metric NAME`, and `--limit N` (1–100). Retained jobs expire according to the
server's configured TTL.

Readable output preserves complete identifiers and shows unavailable values as
`unknown`. Worker and queue results include inspection age, staleness, and errors.
A successfully retrieved inspection response exits zero even if workers are
unavailable. Use `health` for the dependency health gate; it does not assert that
every queue has a worker.

## Offline configuration validation

```bash
uv run lyra-admin config validate ./lyra.toml
uv run lyra-admin --json config validate ./lyra.toml
```

This command only reads the specified TOML and validates its schema and internal
relationships. It requires no server, secrets, network, or writable directories.
It does not verify plugin manifests or service availability. Successful JSON is
`{"valid": true, "schema_version": 2}`. Invalid TOML/schema exits `2`; file I/O
failures exit `1`.

Edit the file directly and restart the API and all workers to apply settings.
See [Deployment](../deployment/) for startup, draining, and replication.

## Job cancellation

`jobs cancel ID` is the only mutable administration operation. It asks for
confirmation when stdin and stderr are terminals. Only `y` or `yes` confirms;
an empty answer, refusal, or EOF leaves the server untouched. JSON and
noninteractive commands require `--yes`:

```bash
uv run lyra-admin --json jobs cancel JOB_ID --yes
```

A timeout or interruption does not prove the server rejected cancellation.
Inspect the job before retrying; the CLI does not automatically retry mutations.
Restart processes using your deployment tooling.

## JSON and exit codes

`--json` writes one complete API response object to stdout, followed by a newline.
It includes all response-model fields, including nulls and observation metadata.
There is no CLI envelope. Errors go to stderr as a JSON object:

```json
{"error": {"kind": "usage", "message": "Set LYRA_ADMIN_API_KEY or supply --admin-api-key."}}
```

An unhealthy readiness response or unsuccessful cancellation can produce
both a response on stdout and an error on stderr. Check the exit code before
assuming success. Help remains plain text, including when `--json` is present.

| Exit code | Meaning |
| --- | --- |
| `0` | Successful command or help. |
| `1` | Request/response failure, unsuccessful health check, or operation failure. |
| `2` | Invalid arguments, configuration, or missing credentials. |
| `3` | Confirmation declined or `--yes` required. |
| `130` | Interrupted; an in-flight mutation may have completed. |

Error kinds are `usage`, `confirmation`, `request`, `operation`, and `interrupted`.
Response JSON follows the installed API client's models. Scripts should use field
names rather than parse human-readable tables. The CLI does not export or apply
configuration files.

## Command coverage

The CLI provides health, status, loaded configuration, jobs, workers, queues,
repositories, catalog, and routing inspection. It also supports job cancellation
and offline configuration validation. Configuration writes, catalog refresh,
and worker restart commands and HTTP APIs are removed.

See the generated [CLI reference](../../reference/generated/cli/) for root,
group, and individual command help, or use `lyra-admin COMMAND --help`.
