# Add Agent Discovery Utilities

## Goal

Make natural-language metropolitan-zone resolution and lexical metric discovery
self-contained through MCP.

## Background from the discussion

Agents currently receive prompts such as "Mexico City" but MCP execution
requires a raw zone code. Search also tokenizes snake_case and accented text
poorly, reducing otherwise valid metric matches.

## Scope

- Add a read-only, idempotent `lyra_lookup_met_zone` MCP tool backed by the
  existing public fuzzy lookup.
- Return the canonical metropolitan-zone code and matched display name.
- Normalize metric search across snake_case, kebab-case, camelCase, Unicode
  accents, case, and repeated tokens.
- Preserve deterministic lexical ranking and candidate explanations.

## Out of scope

- Semantic/vector search, embeddings, LLM-generated synonyms, and plugin-authored
  taxonomy fields.
- Multiple ambiguous location candidates or changes to the database threshold.
- Automatic selection or execution of a metric pair.

## Files or areas likely affected

- MCP contract models and tools.
- `packages/lyra_sdk/src/lyra/sdk/models/metric.py`
- `tests/test_mcp_server.py`

## Required behavior

- An agent can resolve "Mexico City" or a supported misspelling to the same
  canonical lookup response available through REST.
- Unknown locations return a structured, actionable tool error.
- Metric names such as `tree_coverage` match queries such as "tree coverage".
- Accented and unaccented forms normalize consistently.
- Ranking is deterministic across catalog order and repeated calls.
- Lookup and search tools declare strict schemas and read-only/idempotent
  annotations.

## Implementation notes

- Add a lookup method to the MCP backend protocol rather than making an HTTP
  request back into the same process.
- Use Unicode normalization and deterministic token splitting; do not add a
  search dependency for this scope.
- Keep search reasons grounded in the public metric contract.

## Tests and verification

- Use the manifest-declared discovery tests.
- Cover canonical lookup, fuzzy match, no match, snake/kebab/camel tokens,
  accents, stable ordering, schemas, and annotations through the SDK client.

## Step exit checklist

- [ ] Metropolitan-zone name resolution is available through MCP.
- [ ] Unknown lookups produce structured errors.
- [ ] Common identifier styles and accents search correctly.
- [ ] Search ranking remains deterministic and contract-grounded.

## Decision gate before the next step

Proceed only when an MCP-only client can resolve a zone and discover both sample
metrics without calling the REST lookup directly.

## Next-step context

The next step makes the raw JSONL handoff absolute and explicitly authenticated.
