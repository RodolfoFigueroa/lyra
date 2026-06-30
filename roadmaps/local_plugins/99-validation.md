# Final Validation

## Implementation Checklist

- `01-source-contract.md` is complete:
  - `dir://` sources parse and normalize.
  - Directory sources are stored with `ref=None`.
  - Raw filesystem paths remain rejected.
  - Existing GitHub and `file://` behavior is unchanged.

- `02-directory-sync.md` is complete:
  - Directory sources copy snapshots into managed target directories.
  - Uncommitted edits are reflected after pull or refresh.
  - Unchanged directories return `changed=False`.
  - Deleted source files do not survive in the target snapshot.
  - Git is not required for directory sources.

- `03-api-worker-flow.md` is complete:
  - Admin create, list, pull, and refresh flows work for `dir://`.
  - Sync errors are clear and test-covered.
  - Catalog loading still reads manifests without importing plugin code.
  - Worker loading installs copied snapshots from runner install dirs.

- `04-docs-and-examples.md` is complete:
  - Docs describe `dir://` as a development directory source.
  - Docker bind mount requirements are documented.
  - `file://` committed-state behavior is still documented.

## Repository Commands

Run formatting, linting, type checking, and focused tests:

```bash
uv run ruff format
uv run ruff check --fix
uv run ty check
uv run pytest tests/test_plugins.py tests/test_plugin_state.py tests/test_update_plugins.py tests/test_registry_catalog.py tests/test_runner.py
```

Run the full Python test suite:

```bash
uv run pytest
```

Run docs validation if Node dependencies are installed:

```bash
npm run build --prefix docs
```

## End-to-End Scenarios

### Plain Directory Plugin

1. Create a temporary installable plugin directory with:
   - `lyra.plugin.json`
   - `pyproject.toml`
   - a small Python package with a metric entrypoint
2. Add it through the admin API:

```bash
curl -X POST http://localhost:5219/admin/plugin-repos \
  -H "Authorization: Bearer ${LYRA_ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"source":"dir:///absolute/path/to/mock-plugin"}'
```

3. Refresh the catalog.
4. Confirm the metric appears in `/metrics`.
5. Start or restart a worker that consumes the metric queue.
6. Submit a job for the mock metric and confirm it completes.

Pass criteria: the metric is discoverable and executable without the plugin
directory being a git repository.

### Uncommitted Edit Loop

1. Edit `lyra.plugin.json` or the mock plugin entrypoint without committing.
2. Refresh the catalog.
3. Confirm the sync result reports the directory source as changed.
4. Confirm the API or worker observes the edited behavior after refresh and
   worker restart.

Pass criteria: uncommitted edits are reflected after refresh.

### Docker Bind Mount

1. Bind mount the same host plugin directory into API and worker containers at
   the same absolute path, for example `/plugins/mock-plugin`.
2. Configure the source as `dir:///plugins/mock-plugin`.
3. Refresh the catalog and restart workers through the normal flow.

Pass criteria: both API and workers can sync from the same `dir://` path inside
their containers.

## Regression Checks

- `file:///absolute/path/to/repo` still clones local git committed state.
- Uncommitted changes in a `file://` local git repo are still ignored.
- GitHub refs such as `owner/repo@main` still clone the requested ref.
- `dir://` rejects refs.
- Raw paths such as `/plugins/mock-plugin` remain rejected.
- Duplicate enabled source validation still works.
- API catalog refresh still does not import plugin Python modules.
- Worker install failures still skip bad plugins with warnings.

## Pass and Fail Criteria

Pass when all repository commands succeed, the end-to-end scenarios work, and
the regression checks are covered by tests or manual verification.

Fail if any existing GitHub or local git behavior changes unintentionally, if
directory sources require git, if uncommitted directory edits are ignored, or if
workers install directly from the original source directory instead of a managed
snapshot.

