# API Migration Roadmap

These documents are the implementation source of truth for moving Lyra plugin
operations out of read-only deployment config and into admin API endpoints backed
by Lyra-owned state.

Do not treat scattered notes, older roadmap files, or current code behavior as
authoritative once implementation begins. If a decision conflicts with these
documents, update this roadmap first, then implement from the updated text.

## Goal

Lyra should support Docker deployments where:

- `/lyra_data/config/lyra.toml` is an operator-authored, read-only config file.
- secret files are mounted individually and read-only.
- plugin repositories are managed through admin API endpoints.
- metric queue assignments are managed through admin API endpoints.
- Lyra persists plugin operational state in `/lyra_data/state/plugins.toml`.

No backwards compatibility, config migration, or legacy behavior preservation is
required.

## Document Order

Implement the migration in this order:

1. `01-current-state-and-problems.md`
2. `02-target-architecture.md`
3. `03-state-file-contract.md`
4. `04-admin-api-contract.md`
5. `05-runtime-data-flow.md`
6. `06-docker-and-deployment.md`
7. `07-implementation-checklist.md`
8. `08-final-validation.md`

Each numbered file builds on the previous one. The final validation file is the
closure gate for the whole migration.

## Final Target

The final architecture has three layers:

- Deployment config: read-only `lyra.toml` for infrastructure, policy, and
  worker shape.
- Lyra-owned state: writable `/lyra_data/state/plugins.toml` for plugin repos
  and metric routing.
- Admin API: authenticated endpoints for repo operations, catalog refresh, and
  metric routing edits.

