# Directory Sync

## Goal

Implement snapshot syncing for `dir://` sources so a plain directory can be
copied into Lyra-managed plugin target directories.

## Background from the discussion

Directory sources should be useful for local development and mock plugins. They
should copy the current working tree, including uncommitted edits, when the user
pulls or refreshes plugins. They should not become live mounts.

## Scope

- Update sync internals in `lyra_app/plugins.py`.
- Add sync tests in `tests/test_plugins.py`.

## Out of scope

- Do not change admin route response schemas.
- Do not update documentation in this step.
- Do not add a sample plugin to the repository unless a test fixture requires
  one.

## Files or areas likely affected

- `lyra_app/plugins.py`
- `tests/test_plugins.py`

## Required behavior

- First sync of a directory source copies the source directory into
  `target_dir / entry.target_name` and returns `changed=True`.
- Re-syncing without source content changes returns `changed=False`.
- Editing an uncommitted source file causes the next sync to return
  `changed=True` and copy the new content.
- Adding a new source file causes `changed=True`.
- Deleting a source file removes it from the target on the next changed sync.
- Directory sync must not require the source to be a git repository.
- Directory sync must not run `git`.
- GitHub and `file://` local git sync behavior remains unchanged.
- Bulk sync with `raise_on_error=False` logs and skips an invalid directory
  source, matching the current forgiving behavior for malformed or failed repo
  entries.
- Bulk sync or single sync with `raise_on_error=True` raises a useful sync error
  for missing or unreadable directory sources.

## Implementation notes

- Consider splitting git behavior into `_sync_git_repo()` and adding a dispatcher
  such as `_sync_plugin_source()`.
- Add a small `PluginSyncError` exception for non-git sync failures. Preserve
  `subprocess.CalledProcessError` for git failures unless a broader cleanup is
  intentionally included.
- Use a content fingerprint for directory sources:
  - Sort relative file paths for stable output.
  - Include relative path and file content hash.
  - Include symlink target text for symlinks.
  - Avoid mtimes.
  - Exclude transient development artifacts listed in `00-overview.md`.
- Store the last copied fingerprint outside the plugin target directory, for
  example beside the target as `.<target-name>.fingerprint`. Do not write a Lyra
  metadata file into the plugin directory.
- Copy into a temporary directory under the same target parent, then replace the
  managed target directory. This prevents stale files from surviving source
  deletions.
- Use `shutil.rmtree()` only on Lyra-owned target directories and temporary
  directories created under the configured target parent.
- Preserve symlinks with `shutil.copytree(..., symlinks=True)` unless tests or
  runtime behavior show that a stricter policy is needed.

## Tests and verification

- Add tests that sync a plain directory containing `lyra.plugin.json`.
- Add tests for unchanged sync returning `changed=False`.
- Add tests for uncommitted file edits returning `changed=True`.
- Add tests for deleting a source file and confirming the target file is gone.
- Add tests that `.git` and Python cache files are not copied.
- Add tests that local git `file://` sources still ignore uncommitted edits.
- Add tests for missing directory behavior with `raise_on_error=True` and
  `raise_on_error=False`.

Run:

```bash
uv run pytest tests/test_plugins.py
```

## Completion criteria

- `sync_plugin_repo()` and `sync_plugin_repos()` both work for directory sources.
- Directory snapshots include current source edits without requiring git commits.
- The target directory mirrors the source snapshot after changed syncs.
- Existing git sync tests continue to pass.

## Handoff notes for the next step

The next step should wire any new `PluginSyncError` or directory sync failures
through admin routes, catalog refresh, and worker startup paths.

