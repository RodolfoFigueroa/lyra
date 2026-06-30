# Source Contract

## Goal

Add a new explicit `dir://` plugin source kind to parsing, normalization, state
storage, and source serialization.

## Background from the discussion

The app currently loads plugins from GitHub repositories and local git
repositories. Local `file://` sources deliberately ignore uncommitted edits
because they are cloned through git. The new feature should help local plugin
development and executor testing by allowing a plain directory to be used
without git.

## Scope

- Update `lyra_app/plugins.py`.
- Update `lyra_app/plugin_state.py`.
- Add focused tests in `tests/test_plugins.py` and `tests/test_plugin_state.py`.

## Out of scope

- Do not implement copying or fingerprinting in this step.
- Do not change GitHub parsing.
- Do not change `file://` local git behavior.
- Do not update docs yet.

## Files or areas likely affected

- `lyra_app/plugins.py`
- `lyra_app/plugin_state.py`
- `tests/test_plugins.py`
- `tests/test_plugin_state.py`

## Required behavior

- `parse_repo_entry("dir:///absolute/path/to/plugin")` returns a
  `PluginRepoEntry` whose `source_kind` is `directory`.
- `dir://localhost/absolute/path/to/plugin` is accepted and normalized to the
  equivalent `dir:///absolute/path/to/plugin`.
- Relative paths are rejected.
- Params, query strings, and fragments are rejected.
- `@ref` selectors are rejected for directory sources.
- Raw filesystem paths such as `/absolute/path/to/plugin` remain rejected.
- Existing `file:///absolute/path/to/repo` local git parsing continues to pass
  all current tests.
- `normalize_repo_source("dir://...")` stores a normalized `dir://` URI with
  `ref=None`.
- `PluginRepoRecord` rejects a non-null `ref` for directory sources.
- `repo_record_to_source()` returns the stored `dir://` source unchanged.
- Generated ids for directory sources are deterministic and do not collide with
  local git ids for the same basename.

## Implementation notes

- Extend `RepoSourceKind` from `Literal["github", "local"]` to include
  `"directory"`.
- Add a parser helper similar to `_parse_local_repo_entry`, but for `dir`.
- Keep `clone_url` on `PluginRepoEntry` for now to avoid a broad refactor. For
  directory entries, set it to the normalized `dir://` URI or another stable
  display string; later sync code should not use it as a git URL.
- Add a small URI renderer for `dir://` because `Path.as_uri()` always produces
  `file://`.
- Update `display_name` so directory sources are visibly different, for example
  `dir:/absolute/path/to/plugin`.
- Update `target_name` so directory sources use a prefix such as `dir__`.
- Keep local git display names and target names unchanged.

## Tests and verification

- Add parser tests for:
  - `dir:///tmp/mock-plugin`
  - `dir://localhost/tmp/mock-plugin`
  - malformed relative `dir:mock-plugin`
  - `dir:///tmp/mock-plugin@main`
  - `dir:///tmp/mock-plugin?x=1`
  - raw `/tmp/mock-plugin`
- Add plugin state tests for:
  - normalized directory source
  - generated directory id
  - rejecting `ref` on directory source
  - preserving existing `file://` local git expectations

Run:

```bash
uv run pytest tests/test_plugins.py tests/test_plugin_state.py
```

## Completion criteria

- Directory sources can be parsed and stored in plugin state.
- No sync behavior has changed yet.
- All existing GitHub and local git tests still pass.

## Handoff notes for the next step

The next step can assume that `sync_plugin_repos()` receives
`PluginRepoEntry(source_kind="directory", source_path=<absolute path>)` for
valid directory sources.

