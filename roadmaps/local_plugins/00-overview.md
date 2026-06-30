# Local Directory Plugin Sources

## Feature Summary

Add an explicit development-only plugin source for loading a plugin from a plain
local directory without requiring that directory to be a git repository.

Current source kinds remain unchanged:

- `owner/repo` and `owner/repo@ref` clone GitHub repositories.
- `file:///absolute/path/to/repo` clones a local git repository from committed
  state.

New source kind:

- `dir:///absolute/path/to/plugin` copies the current directory contents into
  Lyra-managed plugin catalog and worker install directories at sync time.

This is intended for plugin development, local mock plugins, and testing changes
to the core executor app. It is not intended to be the recommended production
distribution path.

## Agreed Decisions

- Use an explicit `dir://` URI scheme.
- Do not accept raw filesystem paths.
- Do not change existing `file://` behavior. It must continue to mean "local git
  repo, committed state only".
- Directory sources are snapshots copied on refresh or pull, not live mounts.
- Directory sources include uncommitted edits because they are copied from the
  working directory directly.
- API catalog loading must still only read `lyra.plugin.json`; it must not import
  plugin Python code.
- Workers still install from Lyra-owned runner install directories.
- Workers still do not hot-reload in process. The existing catalog refresh and
  worker restart flow remains the activation boundary.
- Docker deployments must work when the same absolute `dir://` path is bind
  mounted into API and worker containers.

## Non-Goals

- No production-grade lockfile or content-addressed plugin release mechanism.
- No branch, tag, or commit refs for `dir://` sources.
- No raw `/some/path` source support.
- No change to the plugin manifest schema.
- No recursive discovery of multiple plugins under a parent directory.
- No runtime file watcher or automatic worker hot reload.
- No direct use of the source directory as the worker install path.

## Assumptions

- The source scheme should be named `dir`.
- The internal source kind should be named `directory`.
- `dir://localhost/absolute/path` may be accepted for parity with current
  `file://localhost/...` handling, but should normalize to `dir:///absolute/path`.
- Source paths should be absolute and resolved with `strict=False` during state
  normalization. A path does not need to exist when it is added to state, but it
  must exist and be a directory when synced.
- Directory target names should be distinct from local git targets, for example
  `dir__mock_plugin__<hash>`.
- Change detection should be content based, not mtime based.
- A small ignore list for transient development artifacts is acceptable:
  `.git`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.ty`,
  `.venv`, `build`, `dist`, `*.egg-info`, and `*.pyc`.
- Symlinks should be preserved rather than dereferenced unless implementation
  work uncovers a stronger reason to reject them.

## Risks

- Directory copy must avoid stale files after source deletions.
- Directory copy must avoid repeated false positives caused by editable install
  artifacts such as `*.egg-info`.
- Directory sync errors will not always be `subprocess.CalledProcessError`; admin
  and refresh error handling may need a generic plugin sync error path.
- Docker usage requires the same absolute source path inside API and worker
  containers.
- Copying very large directories could be slow. This is acceptable for the
  development use case, but docs should warn users not to point at broad project
  roots with large caches.

## Execution Order

1. Add the `dir://` source contract to parsing, normalization, and plugin state.
2. Implement directory snapshot sync with content fingerprinting.
3. Wire directory sync errors and behavior through admin, catalog, and workers.
4. Update docs and examples for local development and Docker bind mounts.
5. Run the final validation checklist in `99-validation.md`.

