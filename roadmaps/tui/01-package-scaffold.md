# Package Scaffold

## Goal

Create the `packages/lyra_tui` workspace package with Textual dependencies, a console entry point, and a minimal app that can start and exit cleanly.

## Background From The Discussion

The TUI should be a first-party package in the existing uv workspace, not a separate repo and not part of `lyra_app`. It should be installable and runnable as an operator tool while keeping Textual out of the API and worker runtime dependencies.

## Scope

- Add `packages/lyra_tui/pyproject.toml`.
- Add package source under `packages/lyra_tui/src/lyra/tui/`.
- Add a console script named `lyra-tui`.
- Add a minimal Textual `App` with header/footer and a placeholder screen.
- Register `lyra-tui` as a workspace source in the root `pyproject.toml` if needed for local development.
- Add focused package-level tests that verify imports and startup wiring without requiring a running Lyra API.

## Out Of Scope

- No real API calls yet.
- No management actions yet.
- No Docker image changes.
- No server route changes.

## Files Or Areas Likely Affected

- `pyproject.toml`
- `uv.lock`
- `packages/lyra_tui/pyproject.toml`
- `packages/lyra_tui/src/lyra/tui/__init__.py`
- `packages/lyra_tui/src/lyra/tui/__main__.py`
- `packages/lyra_tui/src/lyra/tui/app.py`
- `tests/` or `packages/lyra_tui/tests/`, following the repo's preferred test layout

## Required Behavior

- `uv run lyra-tui --help` should show TUI startup options.
- `uv run python -m lyra.tui` should run the same entry path.
- The initial app should open and quit without contacting Lyra.
- The package should depend on `lyra-api` and `textual`.
- The root `lyra-app` runtime dependencies should not include `textual`.

## Implementation Notes

- Use `uv add --package lyra-tui textual lyra-api` or edit package metadata and run `uv lock`, depending on what best fits the current workspace state.
- Prefer `argparse` for the small launcher unless the project adds a CLI framework later.
- Keep the app constructor separate from argument parsing so tests can instantiate the app directly.
- Suggested initial modules:
  - `app.py`: Textual app class.
  - `__main__.py`: parse args and run the app.
  - `config.py`: small runtime configuration dataclass.
- Add command-line options early enough to avoid hard-coded host/auth in later steps:
  - `--host`, default `localhost:5219`
  - `--secure/--no-secure`, default `--no-secure` for local development
  - `--admin-api-key`, default from `LYRA_ADMIN_API_KEY`
  - `--timeout`, default matching the client default unless there is a reason to change it

## Tests And Verification

- `uv run pytest` or a narrower package test command if the repo grows package-local tests.
- `uv run ruff format`
- `uv run ruff check --fix`
- `uv run ty check --fix`
- Manual smoke check:
  - `uv run lyra-tui --help`
  - `uv run python -m lyra.tui --help`

## Completion Criteria

- The package exists and is part of the uv workspace.
- The app can be launched and exited.
- Textual is isolated to the TUI package.
- Basic import/startup tests pass.
- The lockfile reflects the new package and dependencies.

## Handoff Notes For The Next Step

The next step should add the shared app architecture and client adapter. Do not begin screen-specific UI work until configuration, client construction, polling, and error-state patterns are in place.

