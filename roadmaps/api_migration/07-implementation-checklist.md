# Implementation Checklist

Complete the migration in this order.

## 1. State Layer

- Add plugin state models for repo records and metric queue assignments.
- Add load, reload, validate, render, and atomic save helpers.
- Add repo source normalization that converts accepted request formats into
  normalized state.
- Add CRUD helpers for repos and metric routing.
- Add tests for parsing, validation, normalization, atomic writes, and missing
  state-file defaults.

## 2. Config Contract Cleanup

- Remove `repos` from `LyraConfig.plugins`.
- Remove `metric_queues` from `LyraConfig.plugins`.
- Keep `default_queue`, `allowed_queues`, `catalog_dir`, and
  `runner_base_dir`.
- Update `lyra.toml.example`.
- Update config contract tests to reject the removed fields.

## 3. Runtime Wiring

- Change API catalog refresh to read enabled repos and routing from plugin
  state.
- Change catalog refresh auto-assignment to write missing routes to plugin
  state.
- Change API dispatch to use state-owned routing.
- Change worker startup to read enabled repos and routing from plugin state.
- Ensure workers never mutate plugin state.

## 4. Admin API

- Add `/admin/plugin-repos` list, create, update, delete endpoints.
- Add `/admin/plugin-repos/{repo_id}/pull`.
- Replace `/update-plugins` with `/admin/plugin-catalog/refresh`.
- Add `/admin/plugin-routing` list, set, delete endpoints.
- Reuse existing bearer admin auth.
- Add route tests for auth, validation, success responses, and not-found
  behavior.

## 5. Docker And Docs

- Update Docker Compose to mount `lyra.toml` as a read-only file.
- Update Docker Compose to mount each secret as a read-only file.
- Keep `lyra_data:/lyra_data` as the writable runtime volume.
- Update `.env.example` with host mount path variables.
- Update user docs, plugin quickstart, deployment docs, local development docs,
  operations docs, and AI agent guide.

## 6. Integration Tests

- Test catalog refresh with repos from plugin state.
- Test auto-created routing persists to plugin state.
- Test workers load repos and routing from plugin state.
- Test route deletion followed by refresh recreates the route with
  `plugins.default_queue`.
- Test `lyra.toml` can be read from a read-only file mount shape.

## 7. Closure

Run `08-final-validation.md` exactly. The migration is not complete until every
validation item passes.

