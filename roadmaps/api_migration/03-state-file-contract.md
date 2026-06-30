# State File Contract

## Location

Lyra stores plugin operational state in:

```text
/lyra_data/state/plugins.toml
```

The file is Lyra-owned and writable. It is not mounted directly from the host.
It lives inside the durable `lyra_data` Docker volume.

## Format

Use TOML.

TOML is not required for machine parsing, but it is readable, diffable, and
consistent with `lyra.toml`. Operators can inspect or repair it if necessary.

## Schema

Example:

```toml
schema_version = 1

[[repos]]
id = "owner-example-plugin"
source = "owner/example-plugin"
ref = "main"
enabled = true

[[repos]]
id = "local-metrics"
source = "file:///lyra_data/plugins/local/local-metrics"
enabled = false

[metric_queues]
walkability_score = "interactive"
regional_accessibility = "batch"
```

Fields:

- `schema_version`: must be integer `1`.
- `repos`: ordered list of plugin repo records.
- `repos.id`: stable unique identifier used by admin API paths.
- `repos.source`: normalized plugin source without an inline `@ref`.
- `repos.ref`: optional branch, tag, or commit for GitHub sources.
- `repos.enabled`: boolean, defaults to `true` when created by API.
- `metric_queues`: mapping of metric name to queue name.

## Repo Source Rules

The API may accept the same source shapes operators use today:

- `owner/repo`
- `owner/repo@ref`
- `https://github.com/owner/repo`
- `https://github.com/owner/repo@ref`
- `file:///absolute/path/to/repo`

State must store normalized values:

- GitHub source without inline `@ref`.
- GitHub ref in `repos.ref`.
- Local `file://` sources without `repos.ref`.

Local repo sources must be absolute `file://` URIs. They cannot include a ref.

## ID Rules

`repos.id` is stable and unique.

If a `POST /admin/plugin-repos` request does not provide an ID, Lyra generates
one from the normalized repo target name. If that ID already exists, the API must
return a validation error and ask the caller to provide a unique ID.

IDs must be non-empty and URL-path safe:

```text
A-Z a-z 0-9 _ . -
```

## Validation

State validation must reject:

- unknown top-level fields,
- unknown repo fields,
- duplicate repo IDs,
- duplicate enabled repo sources,
- malformed repo sources,
- local repo refs,
- metric queue names outside `plugins.allowed_queues`,
- empty metric names,
- empty queue names.

Disabled repos are allowed to duplicate sources from enabled repos only if their
IDs are different. Enabled duplicate sources are rejected.

## Writes

All writes to `plugins.toml` must be atomic:

1. render complete TOML to a temp file in `/lyra_data/state`,
2. flush and fsync the temp file,
3. replace `/lyra_data/state/plugins.toml`,
4. clean up temp files on failure.

When the state file does not exist, Lyra treats it as:

```toml
schema_version = 1

[metric_queues]
```

and creates it on the first state mutation or catalog auto-assignment.

