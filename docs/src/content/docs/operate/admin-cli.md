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
all other commands require the admin key. Agent credentials and consumer job
event streams are outside this CLI's interface.

## Inspection

| Command | Result |
| --- | --- |
| `health` | Redis and PostgreSQL readiness; exits nonzero if unhealthy. |
| `health --live` | API process liveness. |
| `status` | Administrative service summary. |
| `config-summary` | Effective configuration with secrets omitted. |
| `jobs list` | Up to 50 recent retained jobs, newest first. |
| `workers list` | Worker state and observation metadata. |
| `workers get NAME` | One worker's tasks, statistics, and observation metadata. |
| `queues list` | Routing coverage, worker coverage, and queue depth. |
| `repos list` | Repository identities, sources, refs, and enabled states. |
| `catalog show` | Catalog fingerprint, metric names, plugin sources, and routing. |
| `routing list` | Explicit assignments, allowed queues, and the default queue. |

Filter jobs with `--status queued|running|succeeded|failed|cancelled`,
`--metric NAME`, and `--limit N` (1–100). Retained jobs expire according to the
server's configured TTL.

Readable output preserves complete identifiers and shows unavailable values as
`unknown`. Worker and queue results include inspection age, staleness, and errors.
A successfully retrieved inspection response exits zero even if workers are
unavailable. Use `health` for the dependency health gate; it does not assert that
every queue has a worker.

## Mutations

| Command | Action |
| --- | --- |
| `jobs cancel ID` | Request cancellation of a retained job. |
| `workers restart [--restart-timeout SECONDS]` | Request restart of all workers; default drain timeout is 30 seconds. |
| `repos add SOURCE [--id ID] [--disabled]` | Register a repository. |
| `repos update ID --source SOURCE` | Replace a repository source. |
| `repos enable ID` / `repos disable ID` | Change enabled state. |
| `repos delete ID` | Delete a repository. |
| `repos sync ID` | Synchronize a repository. |
| `catalog refresh` | Synchronize enabled sources and refresh the catalog. |
| `routing set METRIC QUEUE` | Assign a metric to a queue. |
| `routing delete METRIC` | Remove an explicit assignment. |

Every mutation asks for confirmation when stdin and stderr are terminals. Only
`y` or `yes` confirms; an empty answer, refusal, or EOF leaves the server untouched.
Append `--yes` to authorize a mutation without prompting. JSON and noninteractive
commands always require `--yes`.

```bash
uv run lyra-admin routing set metric_name batch
uv run lyra-admin --json repos sync repository_id --yes
uv run lyra-admin --timeout 90 workers restart --restart-timeout 60 --yes
```

The HTTP request timeout and worker drain timeout are separate. A timeout or
interruption during a request does not prove that the server rejected the action;
inspect its state before retrying. The CLI does not automatically retry mutations.

Repository changes refresh the catalog. If the repository change persists but
catalog refresh fails, the CLI prints the response and exits nonzero. It does not
claim rollback. A recommendation to restart workers is displayed but does not
trigger a restart. Successful no-op operations, such as deleting an already absent
routing assignment, exit zero. A worker restart response acknowledges the request;
it does not certify that all workers are healthy again.

## JSON and exit codes

`--json` writes one complete API response object to stdout, followed by a newline.
It includes all response-model fields, including nulls and observation metadata.
There is no CLI envelope. Errors go to stderr as a JSON object:

```json
{"error": {"kind": "usage", "message": "Set LYRA_ADMIN_API_KEY or supply --admin-api-key."}}
```

An unhealthy readiness response or a partially successful mutation can produce
both a response on stdout and an error on stderr. Check the exit code before
assuming success. Help remains plain text, including when `--json` is present.

| Exit code | Meaning |
| --- | --- |
| `0` | Successful command or help. |
| `1` | Request/response failure, unsuccessful health check, or operation failure. |
| `2` | Invalid arguments or missing credentials. |
| `3` | Confirmation declined or `--yes` required. |
| `130` | Interrupted; an in-flight mutation may have completed. |

Error kinds are `usage`, `confirmation`, `request`, `operation`, and `interrupted`.
Response JSON follows the installed API client's models. Scripts should use field
names rather than parse human-readable tables. The CLI does not export or apply
configuration files.

## TUI migration and coverage

The CLI replaces the removed `lyra-tui` distribution and entry point. Install
`lyra-api` and use the following equivalents; automatic refresh and keyboard
navigation have been removed.

| Former TUI capability | CLI equivalent |
| --- | --- |
| Dashboard readiness and service/configuration information | `health`, `status`, `config-summary` |
| Retained jobs and identifying details | `jobs list` (complete IDs and response fields) |
| Cancel selected job | `jobs cancel ID` |
| Workers and queues | `workers list`, `queues list` |
| Restart workers | `workers restart` |
| Plugin repository and catalog views | `repos list`, `catalog show` |
| Add or toggle a repository | `repos add`, `repos enable`, `repos disable` |
| Delete or synchronize a repository | `repos delete`, `repos sync` |
| Refresh catalog | `catalog refresh` |
| View, assign, or remove metric routing | `routing list`, `routing set`, `routing delete` |

See the generated [CLI reference](../../reference/generated/cli/) for root,
group, and individual command help, or use `lyra-admin COMMAND --help`.
