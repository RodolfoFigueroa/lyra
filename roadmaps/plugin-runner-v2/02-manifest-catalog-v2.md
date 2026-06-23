# Step 2 - Manifest Catalog V2

## Goal

Change the core API catalog to read only the simplified v2 manifests. The API remains plugin-code-free and treats JSON Schema as the source of truth.

## Key Changes

- Update the API catalog loader to require v2 `lyra.plugin.json` manifests.
- Remove catalog support for:
  - `parameters`
  - `single` / `batched` callable modes
  - plugin function signature-derived metadata
- Build the in-memory metric catalog from manifest fields:
  - metric name
  - description
  - request schema
  - result schema
  - execution queue
  - entrypoint metadata for worker use only
- Keep catalog fingerprinting based on normalized manifest contents.
- Redefine `/metrics` as schema-backed metadata.
- Delete `/models` or redefine it as a schema-derived endpoint. Preferred v2 default: delete `/models` unless a concrete client need remains.

## Tests

- Catalog loads v2 manifests from plugin repos.
- Catalog rejects duplicate metric names.
- Catalog rejects invalid request/result schemas.
- Catalog fingerprint is stable for unchanged content and changes when manifest content changes.
- Catalog loading never imports plugin modules.

## Done Criteria

- API catalog no longer understands legacy manifest fields.
- `/metrics` returns v2 schema-backed metric metadata.
- No plugin Python code is imported by the API.
