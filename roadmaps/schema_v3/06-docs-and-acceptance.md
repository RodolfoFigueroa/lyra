# 06. Docs And Acceptance

After schema v3 implementation, public documentation must describe v3 as the
only plugin manifest format. Public docs must present compact `inputs` as the
developer-facing authoring model and compiled JSON Schema as the client-facing
contract.

## Public Docs To Update

Update these docs from the roadmap source of truth:

- `docs/src/content/docs/plugin-quickstart.md`
- `docs/src/content/docs/plugin-manifests.md`
- `docs/src/content/docs/metric-output-design.md`
- `docs/src/content/docs/spatial-plugin-inputs.md`
- `docs/src/content/docs/plugin-author-checklist.md`
- Generated API reference pages for `lyra-sdk` models

Quickstart examples must use schema v3:

```json
{
  "schema_version": 3,
  "plugin": {
    "name": "example-lyra-plugin",
    "version": "0.1.0"
  },
  "metrics": [
    {
      "name": "example_metric",
      "description": "Return the submitted value for each input feature.",
      "queue": "interactive",
      "entrypoint": "example_plugin.runner:run",
      "inputs": {
        "location": { "kind": "location" },
        "value": { "kind": "number" }
      },
      "output": {
        "kind": "table",
        "columns": [
          {
            "name": "value",
            "type": "number",
            "unit": "dimensionless",
            "description": "Submitted numeric value."
          }
        ]
      }
    }
  ]
}
```

## Documentation Requirements

Public docs must state:

- Plugin authors write semantic `inputs`.
- Lyra compiles `inputs` into effective JSON Schema.
- `/metrics` exposes compiled effective JSON Schema for clients.
- Spatial fields are declared with `kind: "location"` or `kind: "bounds"`.
- Batch fields are metric-local arguments that can drive dynamic table columns.
- Batch item `key` and `label` are Lyra-owned protocol fields.
- Plugin-specific batch semantics belong in `value`.
- `json_schema` is an escape hatch only for plugin-owned inputs.

Public docs must not instruct authors to write:

```json
{
  "request_schema": {
    "type": "object",
    "properties": {}
  },
  "spatial_inputs": {
    "location": "location"
  },
  "execution": {
    "queue": "interactive"
  }
}
```

## Acceptance Criteria

Schema v3 work is complete when all of these are true:

- `lyra.plugin.json` examples use `schema_version: 3`.
- The active manifest loader accepts only v3 authoring manifests.
- Plugin authors define `inputs`; they do not define top-level
  `request_schema`.
- Lyra compiles effective JSON Schema for `/metrics` and `POST /jobs`.
- Spatial wrapper schemas are injected by Lyra.
- Batch `key`, optional `label`, item shape, `minItems`, `uniqueItems`, and
  `additionalProperties` are injected by Lyra.
- Batch key uniqueness is validated by the API before jobs are queued.
- Dynamic columns use `name` and `description` in public output metadata.
- `batching_reason`, `name_template`, `description_template`,
  `spatial_inputs`, `request_schema`, and `execution.queue` are absent from the
  developer-facing manifest.
- Static table, dynamic table, mixed table, and file metrics have passing tests.
- Generated public docs and API references match the implemented SDK models.

## Verification Commands

Run the project validation commands after implementation:

```bash
uv run pytest
ruff format
ruff check
ty check
```

Regenerate API docs before running the final docs check:

```bash
uv run python docs/scripts/generate_api_docs.py
```

## Final Review Checklist

Before marking the schema v3 work complete, review:

- A new plugin author can copy the quickstart manifest and run local validation.
- A client developer can inspect `/metrics` and see the complete JSON Schema they
  need for job submission.
- A worker developer can understand table and file result validation from the
  compiled output contract.
- Every v3 roadmap example is represented by at least one automated test.
