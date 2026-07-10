# Lyra Agent Bridge Hardening

## Goal

Turn Lyra's working MCP metric bridge into a standards-compliant, authenticated,
reproducible, and resource-controlled interface for external agents while
keeping statistical analysis in the external agent runtime.

## Agreed scope

- Replace the handwritten MCP protocol transport with the stable official Python
  MCP SDK and verify it with a real SDK client.
- Replace `LYRA_MCP_API_KEY` with one `LYRA_AGENT_API_KEY` used by MCP and all
  job, event, descriptor, and result routes.
- Keep health, metric catalog, and metropolitan-zone lookup routes public; keep
  the admin credential and admin routes separate.
- Add strict MCP input and output contracts, tool annotations, protocol security,
  and finite numeric validation.
- Persist compact run provenance and expose reproducible result descriptors.
- Implement real idempotency and Redis-backed submission rate limiting.
- Add metropolitan-zone lookup and normalized lexical metric search for agents.
- Return absolute, authenticated JSONL download handoffs.
- Keep results ephemeral and Redis-backed for this version.

## Settled decisions

- Breaking changes are allowed. Do not add compatibility shims, migrations,
  aliases for removed configuration, or dual old/new behavior.
- Use the official stable Python MCP SDK line rather than extending the custom
  JSON-RPC implementation. Do not adopt a prerelease SDK line.
- Use one shared agent credential for the current trusted-agent deployment.
  OAuth, per-user identity, and multi-tenant ownership are deferred.
- Plugin parameters are public metric inputs and do not contain secrets, so the
  validated unresolved request may be retained in provenance.
- Do not persist resolved GeoJSON in provenance.
- JSONL and current result TTLs are sufficient; durable storage, signed URLs,
  and Parquet are deferred.
- Preserve the boundary that Lyra computes metrics and external code performs
  joins, correlations, regressions, and other analysis.

## Security and data invariants

- The agent token must never appear in descriptors, tool results, logs, config
  summaries, or download URLs.
- An admin token must not authorize agent routes, and an agent token must not
  authorize admin routes.
- Public routes are limited to health, catalog/discovery metadata, and location
  lookup. All job lifecycle and result data require the agent token.
- The Streamable HTTP endpoint must enforce the official transport's Origin,
  protocol-version, lifecycle, notification, and content-negotiation behavior.
- Idempotent concurrent requests must dispatch at most one job.
- Only newly accepted submissions consume rate-limit capacity; safe idempotent
  replays do not.
- Job provenance has the same minimum retention as its job and result records.
- Feature identity namespaces and versions are emitted only when Lyra can derive
  them from authoritative resolver metadata; they are never guessed from IDs.
- Do not edit the `[tool.ruff.lint]` section in `pyproject.toml`.

## Result provenance contract

The terminal descriptor should carry a schema version, metric name, public
catalog fingerprint, plugin name and version, validated unresolved spatial and
non-spatial inputs, creation and completion timestamps, expanded result column
contracts, and row-identity metadata when available. This contract applies to
successful, failed, and cancelled jobs where the corresponding metadata exists.

Metropolitan-zone table runs currently resolve to 2020 AGEB features indexed by
`cvegeo`; record that namespace and version through explicit resolver metadata.
For arbitrary GeoJSON or unknown sources, leave optional identity fields absent.

## Submission controls

Idempotency is scoped to the shared agent principal. Reusing a key with an
equivalent validated metric request returns the original job with an explicit
`reused` marker. Reusing it with a different request fails with a conflict. The
idempotency record lifetime is at least the configured job-store lifetime.

Rate limiting is enforced in the shared job-submission path so REST and MCP
cannot bypass or double-count it. Use a Redis-backed fixed window with
configurable defaults of 10 new submissions per 60 seconds. Return REST `429`
with `Retry-After` and a structured MCP `rate_limited` tool error. Invalid,
conflicting, and idempotent replay requests do not consume capacity.

## Deferred work

- OAuth, multiple agent principals, per-job ownership, and scoped cancellation.
- Durable result promotion, object storage, signed URLs, CSV, and Parquet.
- Multi-metric batch submission and server-side analysis utilities.
- Semantic/vector search, plugin-authored taxonomy fields, and automatic metric
  compatibility scoring.
- Dynamic per-metric MCP tools.

## Risk and audit posture

This roadmap uses high-assurance posture. Transport, authentication, schemas,
persistence, idempotency, and rate-control steps require an audit on every run.
Steps remain narrow and use focused tests, while repository-wide tests, Ruff,
format checking, and Ty are reserved for final validation and inherited runner
checks.

The official MCP dependency may require approved network access through `uv`.
No roadmap step may write outside the repository. Default model and prompt
budgets follow the schema-version-6 contract; broad provenance steps receive
high reasoning effort and explicit prompt-size inspection before execution.

## Implementation order

1. Establish a conformant official transport before changing public tools.
2. Establish the single agent authentication boundary.
3. Make tool contracts strict and machine-readable.
4. Persist run provenance before exposing it in result descriptors.
5. Add idempotency before rate limiting so replays can be excluded correctly.
6. Add agent discovery and authenticated download ergonomics.
7. Update operator and agent documentation, then run final validation.
