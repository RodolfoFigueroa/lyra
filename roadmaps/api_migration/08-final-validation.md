# Final Validation

Use this file as the closure gate for the API migration. All items must pass.

## Static Contract Checks

- `lyra.toml.example` contains no plugin repo list.
- `lyra.toml.example` contains no `[plugins.metric_queues]` table.
- `LyraConfig` no longer accepts `[plugins].repos`.
- `LyraConfig` no longer accepts `[plugins.metric_queues]`.
- `.env.example` documents Docker host file mount variables, not Lyra runtime
  config variables.
- Docs no longer instruct users to add plugin repos or metric routes by editing
  `lyra.toml`.

## State File Checks

- Starting with no `/lyra_data/state/plugins.toml` produces a valid empty state.
- Adding a plugin repo through the admin API creates
  `/lyra_data/state/plugins.toml`.
- The state file is valid TOML and includes `schema_version = 1`.
- Atomic writes leave no temp files after success.
- Invalid repo sources, duplicate repo IDs, and invalid queues fail with clear
  errors.

## API Workflow Checks

Run this workflow against the Docker stack:

1. Start the stack with read-only file mounts for `lyra.toml` and all secrets.
2. `GET /admin/plugin-repos` returns an empty list.
3. `POST /admin/plugin-repos` adds a plugin repo.
4. `POST /admin/plugin-catalog/refresh` syncs the repo and builds the catalog.
5. Missing metrics are auto-assigned to `plugins.default_queue` in
   `/lyra_data/state/plugins.toml`.
6. `GET /admin/plugin-routing` returns the new assignments.
7. `PUT /admin/plugin-routing/{metric_name}` changes a queue assignment.
8. Restart the matching worker pool.
9. Submit a job for the metric and confirm dispatch uses the updated queue.
10. `DELETE /admin/plugin-repos/{repo_id}` removes the repo from state.

## Docker Checks

- API service mounts:
  - `lyra_data:/lyra_data`
  - `${LYRA_CONFIG_FILE}:/lyra_data/config/lyra.toml:ro`
  - `${LYRA_POSTGRES_PASSWORD_FILE}:/lyra_data/secrets/postgres_password:ro`
  - `${LYRA_ADMIN_API_KEY_FILE}:/lyra_data/secrets/admin_api_key:ro`
  - `${LYRA_SERVICE_ACCOUNT_FILE}:/lyra_data/secrets/service-account.json:ro`
- Every worker service uses the same mounts.
- `/lyra_data/state/plugins.toml` is writable inside the named volume.
- The application does not attempt to write to `/lyra_data/config/lyra.toml`.

## Automated Checks

Run:

```bash
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest
```

All commands must pass.

## Completion Criteria

The migration is complete only when:

- all static contract checks pass,
- all state file checks pass,
- the API workflow succeeds,
- Docker read-only file mounts work,
- all automated checks pass,
- docs and examples describe only the new admin API workflow.

