# Admin API Contract

All endpoints in this document require the existing bearer admin API key.

Use `/admin/...` paths for the new plugin operations. The old root-level
`/update-plugins` route is replaced by `/admin/plugin-catalog/refresh`.

## Plugin Repo Endpoints

### `GET /admin/plugin-repos`

Returns configured repos in state-file order.

Response:

```json
{
  "repos": [
    {
      "id": "owner-example-plugin",
      "source": "owner/example-plugin",
      "ref": "main",
      "enabled": true
    }
  ]
}
```

### `POST /admin/plugin-repos`

Adds one repo to state. This does not pull the repo or refresh the catalog.

Request:

```json
{
  "id": "owner-example-plugin",
  "source": "owner/example-plugin@main",
  "enabled": true
}
```

Rules:

- `id` is optional.
- `enabled` defaults to `true`.
- `source` is normalized before writing state.
- duplicate IDs or duplicate enabled sources return HTTP 422.

Response returns the created repo record.

### `PATCH /admin/plugin-repos/{repo_id}`

Updates mutable fields on an existing repo.

Request:

```json
{
  "source": "owner/example-plugin@v1.2.0",
  "enabled": false
}
```

Rules:

- omitted fields are unchanged.
- changing source normalizes source and ref.
- duplicate enabled sources return HTTP 422.
- unknown repo IDs return HTTP 404.

Response returns the updated repo record.

### `DELETE /admin/plugin-repos/{repo_id}`

Deletes a repo from state. This does not delete existing checkout directories in
`/lyra_data/plugins`. Cleanup of old checkout directories is out of scope for
this migration.

Unknown repo IDs return HTTP 404.

Response:

```json
{
  "deleted": true,
  "repo_id": "owner-example-plugin"
}
```

### `POST /admin/plugin-repos/{repo_id}/pull`

Syncs one enabled repo into the catalog checkout directory.

Rules:

- disabled repos return HTTP 409.
- unknown repo IDs return HTTP 404.
- git failures return HTTP 502 with a readable detail.

Response:

```json
{
  "repo_id": "owner-example-plugin",
  "changed": true,
  "display_name": "owner/example-plugin"
}
```

## Catalog Endpoint

### `POST /admin/plugin-catalog/refresh`

Replaces the old `POST /update-plugins` workflow.

Behavior:

- sync all enabled repos,
- parse manifests,
- auto-create missing metric routing entries with `plugins.default_queue`,
- rebuild the API catalog registry,
- request worker restart using the existing worker-control mechanism.

Query:

- `timeout`: seconds to wait for in-flight tasks before forced restart. Default
  remains `30.0`.

Response:

```json
{
  "updated_plugins": ["owner/example-plugin"],
  "catalog_changed": true,
  "previous_catalog_fingerprint": "old",
  "catalog_fingerprint": "new",
  "assigned_metric_queues": ["walkability_score"],
  "message": "Updated 1 plugin repo(s): owner/example-plugin."
}
```

## Metric Routing Endpoints

### `GET /admin/plugin-routing`

Returns state-owned routing assignments.

Response:

```json
{
  "metric_queues": {
    "walkability_score": "interactive",
    "regional_accessibility": "batch"
  },
  "allowed_queues": ["interactive", "batch"],
  "default_queue": "interactive"
}
```

### `PUT /admin/plugin-routing/{metric_name}`

Creates or replaces a metric queue assignment.

Request:

```json
{
  "queue": "batch"
}
```

Rules:

- queue must appear in `plugins.allowed_queues`.
- metric name must be non-empty.
- the endpoint may accept unknown metric names so operators can preassign routes
  before a catalog refresh.

Response returns the updated assignment.

### `DELETE /admin/plugin-routing/{metric_name}`

Deletes a route from state.

Rules:

- unknown metrics are idempotent and return `deleted: false`.
- deleting a route for a currently cataloged metric means the next catalog
  refresh will recreate it with `plugins.default_queue`.

Response:

```json
{
  "deleted": true,
  "metric_name": "walkability_score"
}
```

## Worker Restart Contract

Repo and routing changes do not hot-reload running workers.

Operators should use `POST /admin/plugin-catalog/refresh` after repo changes.
If they edit routing without refreshing the catalog, they must restart worker
pools explicitly before running workers observe the changed routing.

