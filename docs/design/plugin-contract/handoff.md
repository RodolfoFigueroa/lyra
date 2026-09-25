# Plugin contract redesign handoff

Status: **sessions 1–4 complete; final verification passed**.

Read [contract.md](contract.md) first, then [examples.md](examples.md). The contract
owns all normative decisions; this file records progress and implementation order.
These internal documents are deliberately outside the published documentation
tree. Published guides describe the implemented SDK; these files retain the design and
verification history.

## Decisions locked in the documentation session

- Documentation specimens only; no prototype SDK or speculative failing tests.
- Ordinary lists, fixed output columns, and no special keyed batching.
- Pydantic models declare parameters once; compatible existing models are allowed.
  Root and nested object models must reject unknown fields.
- Canonical spatial arguments and optional runtime context remain separate.
- One generated format-5 manifest; no old format reader or compiled counterpart.
- Native DataFrame/Path output, with shared local/worker normalization.
- Existing output derivations remain; the database-interface generator is untouched.
- No backwards compatibility, migrations, runtime shims, or new dependencies.

## Acceptance matrix for implementation sessions

| Scenario | SDK/session 2 | Integration/session 3 | Completion/session 4 |
| --- | --- | --- | --- |
| E1 scalar adapter | Registration, defaults, direct call, native table normalization | Catalog, submission, spatial resolution, worker result | Published example and end-to-end regression |
| E2 existing model | Compatible subclass accepted; original configuration unchanged; incompatible nested models rejected | Typed parameters reach handler | Reuse documentation and public imports |
| E3 nested/list/null | Full-root schema definitions, ordinary lists, required/null/default rules | API 422 errors and worker model construction | No batch declarations/templates or reverse compiler remain |
| E4 location + bounds | Canonical handler/schema, fixed columns | REST execution, MCP discovery and explicit run rejection | Document MCP's execution boundary |
| E5 file/no parameters | Optional parameters/context rules and Path normalization | File access, content type, retention/provenance | File example and invalid-path regression |
| E6 semantic validator | Shared preparation raises MetricInputError with context | Accepted request becomes invalid_input failure; no handler call | Clear schema-vs-semantic validation documentation |
| Negative checklist | Definition/default/example/schema/result errors | Correct HTTP/job/MCP error boundaries | Consolidated acceptance coverage |
| Manifest consistency | Deterministic output and CLI checks | API never imports plugin; worker verifies live definition | Remove old exports/models/compiler/tests |
| Area derivations | Shared normalizer uses supplied areas and existing tolerance | API computes areas and worker appends derived columns | No regression in geographic identity or units |

## Next sessions

### Session 2: SDK implementation

Implement authoring conventions, supported-model validation, complete schema
generation, format-5 manifest models, and shared parameter/result operations.
Keep schema generation in the SDK and domain-independent result validation in a
shared SDK layer; introduce no dependency on application modules or live services.
Use the existing DataFrame protocol approach to avoid a new pandas dependency.

Add focused executable tests derived from the specimens. Test generator output
and actual validator behavior, not literal snapshots of incidental schema
formatting. Preserve meaningful column ordering and verify self-contained refs.
Update plugin CLI construction/inspection as necessary to exercise the SDK.

The branch can be temporarily incompatible with application imports; report this
explicitly instead of adding compatibility aliases or weakening checks. Commit a
handoff that identifies every remaining integration failure before the next session.

### Session 3: Application integration

Update registry/catalog loading, spatial submission plumbing, worker invocation,
native result handling, and provenance to the new manifest and nested parameters.
Wire the worker to the same preparation/normalization used by SDK tests.

Update MCP parameter nesting without changing the current single-spatial execution
limit. Adapt administrative views, the plugin CLI, example plugin, and affected
test helpers/fixtures. Preserve current job observation and cancellation behavior.
Exercise all six specimens through appropriate API/worker paths, with MCP execution
coverage for single-spatial metrics and a clear dual-spatial rejection.

### Session 4: Removal and final verification

Audit and remove old semantic-input models, dual manifest APIs, batch machinery,
result-return alternatives, stale names/exports, and obsolete tests/docs. Replace
published authoring examples only once the new workflow works. Keep the database
interface generator unchanged. Record any actual deviation from the contract and
its justification; do not introduce an additional authoring style silently.

Run the repository's required Python checks during implementation sessions:
`uv run ruff format .`, `uv run ruff check .`, `uv run ty check`, and relevant tests.
For the full suite, use sandbox escalation with:

```sh
uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
```

Do not build/install wheels or perform distribution smoke checks. Keep work on
one coordinated feature branch and merge only when application integration and
verification are complete. None of these later implementation tasks were carried
out as part of the documentation step.

## Verification of this documentation step

Completed on 2026-09-24, using the existing project environment:

| Check | Command/method | Result |
| --- | --- | --- |
| Specimen syntax | `uv run python -` with `ast.parse`, `json.loads`, and `tomllib.loads` | Passed: 9 Python blocks, 12 JSON blocks, and 1 TOML block; no Python ellipsis placeholders. |
| Internal links | `uv run python -` resolving Markdown file links | Passed: all 8 local links across the three documents resolve. |
| Complete manifest specimen | Installed Pydantic root-model generation and `Draft202012Validator.check_schema` | Passed; local schema references resolve, R1/R2 pass, and R3–R6 fail as specified. |
| Remaining requests and model semantics | `uv run python -` evaluating only standard Pydantic model declarations and generated request schemas | Passed: all 11 request outcomes across six schemas, defaults/examples, reuse without mutation, nested/list/null failures, and E6 semantic rejection. |
| Existing documentation contracts | `uv run pytest -q tests/test_docs_contract.py` | Passed: 13 tests in 1.22 seconds. |

The schema checks use installed Pydantic/jsonschema and existing spatial models.
The model check supplies a temporary standard Pydantic base with the specified
configuration; it does not implement decorators, dispatch, manifests, or result
normalization. These checks establish specimen consistency, not the existence or
correctness of the proposed SDK. Future imports, direct-call tests, normalization,
CLI execution, and end-to-end scenarios remain acceptance work for sessions 2–4.

No tracked Python code, application settings, dependencies, published documentation,
or generated production artifacts changed. Python formatting/lint/type checks and
the full test suite were not run for this documentation-only step. No check failed
or was blocked, and no distribution smoke checks were performed.

## Known boundaries

The specimens are deterministic representative computations, not real external
indicator repositories. They establish integration ergonomics without claiming
to validate a particular scientific algorithm. No user workload or external
plugin was executed. The SDK and application integration are implemented on the feature branch; see
the session-3 results below.

No product decisions remain open for the next session. Implementation must follow
the contract's stated capability limits; requests to expand those limits are
separate design changes.


## Session 2 implementation and verification

Implemented on 2026-09-24. This is an intentionally breaking intermediate branch,
not a deployable application. The failures below are introduced integration work,
not pre-existing failures and not ignored checks.

### Completed SDK work

- Replaced `models/plugin_v4.py` and the semantic-input compiler with
  `models/plugin.py`: one format-5 manifest, static table/file declarations, and
  area-fraction metadata. Updated dependent SDK job and catalog models; catalog
  callers must now use `MetricInfo`.
- Reworked `plugin.py` around ordinary typed parameter models, explicit handler
  registration, unchanged decorated callables, `prepare_parameters`, native
  invocation, and `normalize_result`. Removed `Input`, batch authoring, compiled
  manifests, and `PluginResult`; no compatibility aliases were added.
- Added `parameters.py`, `schema.py`, `results.py`, and `errors.py` for capability
  validation, one-pass Pydantic request schemas, local references, path-bearing
  errors, and service-independent native result normalization. SDK imports do
  not load pandas. No dependencies or database generator changes were needed.
- Updated plugin CLI generation/checking/inspection. Manifest JSON is sorted,
  two-space indented, newline terminated, and includes output discriminator and
  nullable defaults while omitting empty derivations.
- Implemented all six specimens in `tests/fixtures/contract_plugin/`. Replaced
  old SDK compiler/authoring tests with parameter, request, native invocation,
  result, and CLI tests. Preserved loader and CLI pre-commit coverage.

### Verification results

| Command | Outcome |
| --- | --- |
| `uv run ruff format .` | Passed. |
| `uv run ruff check .` | Passed, no suppressions or rule changes. |
| `uv run ty check` | Failed: 25 diagnostics in remaining application/client/example integration callers listed below. |
| `uv run ty check packages/lyra_sdk tests/test_sdk_plugin.py tests/test_sdk_parameters.py tests/test_sdk_results.py tests/test_plugin_cli.py tests/test_plugin_loader.py tests/fixtures/contract_plugin` | Passed. |
| `uv run pytest -q tests/test_sdk_plugin.py tests/test_sdk_parameters.py tests/test_sdk_results.py tests/test_plugin_loader.py tests/test_plugin_cli.py tests/test_sdk_pandas_typing.py` | Passed: 108 tests. |
| `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml` (escalated) | Interrupted by 18 collection errors from remaining old-contract imports. |
| Same full-suite command with `--continue-on-collection-errors` (escalated) | 327 passed, 2 failed, 18 collection errors; generated `coverage.xml`. This flag retains errors and only permits collecting coverage from runnable tests. |
| Fresh `uv run python` process importing `lyra.sdk` and checking `sys.modules` | Passed: pandas is not imported. |
| `git diff --check` | Passed. |

The baseline SDK/loader/CLI suite before implementation passed 89 tests. The new
focused tests cover all 11 documented request outcomes, the six direct specimens,
semantic validation, unsupported authoring capabilities, local schema refs,
mutable model defaults, parameterless and dual-spatial contracts, native dispatch,
per-column numeric extraction, nullable cells, invalid axes/scalars, area fractions,
and file containment/suffix/symlink checks. API/worker/MCP execution is unverified
until session 3. No package distribution checks were performed.

### Session 3 integration inventory

The 25 repository-wide type diagnostics are confined to these files:

- `examples/lyra-plugin/smoke_plugin/metrics.py`: old `Input`, output types, and
  decorator `inputs=`. Rewrite the actual example and regenerate its manifest.
- `lyra_app/job_submission.py`, `lyra_app/spatial_inputs.py`: old manifest/spatial
  imports; update nested parameters while preserving unresolved input hashing.
- `lyra_app/registry.py`, `lyra_app/routes/metrics.py`: old manifest and
  `MetricInfoV4` imports; consume format 5 and fingerprint the canonical schema.
- `lyra_app/worker.py`: old output models, `PluginResult`, and
  `compiled_manifest`. Compare the live format-5 manifest at startup, invoke
  prepared handlers, then normalize native results using the SDK and job context.
- `lyra_app/mcp/models.py`, `lyra_app/mcp/results.py`, `lyra_app/mcp/tools.py`:
  old output/catalog imports; nest parameters and retain explicit dual-spatial
  execution rejection.
- `packages/lyra_api/src/lyra/api/client/async_.py`, `client/endpoints.py`,
  `client/sync.py`: replace `MetricInfoV4` and adapt relevant client contracts.
- `tests/test_batched_table_outputs.py`, `test_mcp_server.py`, `test_runner.py`,
  `test_spatial_inputs.py`: old SDK imports and removed batch semantics.

Full-suite collection errors affect `tests/test_admin_cli.py`,
`test_admin_operations.py`, `test_api_client_jobs.py`,
`test_batched_table_outputs.py`, `test_client_io.py`, `test_client_policies.py`,
`test_docs_contract.py`, `test_jobs_route.py`, `test_main_lifespan.py`,
`test_mcp_server.py`, `test_metrics_route.py`, `test_observability_routes.py`,
`test_plugins.py`, `test_public_imports.py`, `test_registry_catalog.py`,
`test_runner.py`, `test_spatial_inputs.py`, and `test_startup_configuration.py`.
These import the remaining format-4 application/client modules directly or
indirectly. `test_public_imports.py` now checks `MetricParameters`, but its
application-client imports still prevent collection.

The two executable failures in `tests/test_job_store.py` are
`test_table_descriptor_captures_static_and_batched_provenance` and
`test_table_descriptor_expands_fractional_area_column_contract[True]`.
Both still construct removed `batched_columns`; static provenance and static
area derivation pass. Replace batch expectations with fixed-column provenance
coverage during integration, rather than reintroducing the old model.

Keep the existing SDK terminal transport models. Their old convenience
constructors are not accepted plugin return values; the new native normalizer
rejects terminal objects, Series, and dictionaries. Audit these constructors and
remaining documentation/fixtures in session 4. Published docs and the production
example have deliberately not been advertised as working with the new SDK yet.

### Known upstream schema-generation limitation

Installed Pydantic traverses a literal dictionary default containing a `$ref` key
as if it were a schema reference, and can raise `KeyError` during
`model_json_schema`. Lyra surfaces this as `PluginDefinitionError` with the metric
and request-schema context. Its own schema walker correctly treats defaults and
examples as data. A regression test records the limitation; no custom Pydantic
schema generator or default-rewriting workaround was introduced. Ordinary dict
parameters and defaults are supported, but this special default shape remains an
upstream limitation relative to the intended JSON-value capability.

## Session 3 implementation handoff

Application integration completed on 2026-09-24. The pre-existing session-2 SDK
changes were preserved and integrated without compatibility aliases or shims.

### Implemented

- Registry and catalog routes consume the single format-5 manifest directly.
  API discovery remains import-free; worker startup regenerates the live manifest
  and rejects mismatches. Existing snapshot, routing, installation, and source
  integrity checks remain in place.
- Submission validates the complete unresolved request schema, preserves nested
  `parameters` without injecting defaults, resolves only canonical spatial fields,
  and retains the original request for hashing and provenance.
- Workers invoke `PluginDefinition` for parameter construction and handler
  dispatch, then use its shared native-result normalizer. SDK input/result errors
  become `invalid_input`/`invalid_result` failures with field paths. The old worker
  result parser, table validators, batch expansion, and file validator were removed.
  Queue claims, cancellation, progress, database errors, atomic result persistence,
  file lifetime, and static area-fraction derivations retain their existing behavior.
- MCP nests ordinary parameters, includes `{}` for declared parameter models,
  omits empty arguments for parameterless metrics, and rejects nonempty arguments
  for those metrics. An ordinary parameter named `location` stays nested. Dual
  spatial metrics remain discoverable and explicitly unsupported for MCP execution;
  REST executes them. Result projection uses static effective column contracts.
- API clients use unversioned catalog types. Search indexes nested parameter
  descriptions and names in the canonical schema, including local definitions.
- The runnable smoke plugin now declares parameter models and returns DataFrames
  or Paths. Its format-5 manifest was regenerated. Pandas is declared in the example
  plugin's dependencies; no application or SDK dependency was added.
- Migrated registry, route, worker, MCP, client, and provenance fixtures to the new
  contract. Removed tests exclusively concerned with deleted batch expansion and
  templates. Native scalar/axis/file/derivation validation remains covered by the
  shared SDK tests and worker integration tests; ordinary list and static-output
  tests replace batch-specific API/provenance expectations.

### Integration acceptance and verification

All six contract specimens now pass through real submission, spatial resolution,
worker execution, and stored results/provenance using controlled Redis and queue
fakes. Coverage includes defaults applied only at execution, compatible existing
models, nested lists with repeated values, required null, dual spatial inputs,
parameterless file output, and semantic model-validation failure after submission.
Additional regressions verify schema failures before queueing, legacy manifest
rejection, nested MCP arguments, and parameterless MCP behavior. Existing operational
and administrative CLI tests pass with the integrated contract.

Final checks in the existing project environment:

| Command | Result |
| --- | --- |
| `uv run ruff format .` | Passed; 170 files unchanged on the final run. |
| `uv run ruff check .` | Passed. |
| `uv run ty check` | Passed repository-wide. |
| `uv run pytest -q tests/test_sdk_plugin.py tests/test_sdk_parameters.py tests/test_sdk_results.py tests/test_plugin_loader.py tests/test_plugin_cli.py tests/test_sdk_pandas_typing.py` | Passed: 108 tests before application integration. |
| `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml` (escalated) | Passed: 962 tests; 92% aggregate coverage; generated `coverage.xml`. |
| `git diff --check` | Passed. |

Intermediate integration failures were corrected. No outstanding verification
failures or skipped required checks remain. No distribution checks were performed.

### Session 4 scope at the end of session 3

Audit published documentation and public exports/convenience constructors against
this now-working authoring contract. The example implementation is updated, but
older prose and request snippets still need the planned documentation pass.
Terminal transport models remain necessary; their convenience constructors must
not be described as accepted plugin return values. Complete the final removal
and public-API audit from the original plan, keeping the database interface
generator unchanged. The session-2 upstream Pydantic dictionary-default `$ref`
limitation documented above is unchanged.

## Session 4 cleanup and final verification

Completed on 2026-09-24, preserving the existing sessions 1–3 implementation.

- Removed `TableJobResult.from_dataframe`, `from_series`, and `from_mapping`,
  their constructor-only protocols, and conversion helpers. Transport models
  retain unique-axis and rectangular-shape validation. Plugins return native
  DataFrames or Paths through the shared normalizer.
- Audited canonical public exports and removed-interface boundaries. Added
  regressions for the exported authoring API, absent legacy interfaces, transport
  validation, and pandas normalization preserving integer and float column types.
  A fresh SDK import does not load pandas.
- Updated published authoring, architecture, quickstart, REST, Python-client, MCP,
  and troubleshooting guides for the implemented format-5 contract. Examples now
  use nested parameters, explain defaults and semantic validation, and distinguish
  parameterless metrics and MCP's single-spatial-input execution boundary.
- Added executable checks for the documented adapter and request examples against
  the real example manifest, plus MCP input/output schema checks. The adapter runs
  locally with a fake context and no external services.
- Regenerated documentation and verified the example manifest. The database
  interface generator remains unchanged. No new dependencies or compatibility
  shims were introduced.

### Final verification results

| Command | Result |
| --- | --- |
| `uv run ruff format .` | Passed; 170 files unchanged on the final run. |
| `uv run ruff check .` | Passed. |
| `uv run ty check` | Passed repository-wide. |
| `uv run pytest -q tests/test_public_imports.py tests/test_docs_contract.py tests/test_sdk_pandas_typing.py tests/test_sdk_results.py tests/test_sdk_parameters.py tests/test_sdk_plugin.py tests/test_plugin_cli.py tests/test_plugin_loader.py` | Passed: 150 tests. |
| `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml` (escalated) | Passed: 988 tests; 93% aggregate coverage; generated `coverage.xml`. |
| `uv run lyra-plugin check-manifest --project examples/lyra-plugin` | Passed. |
| `npm run generate --prefix docs` | Passed. |
| `npm run check --prefix docs` | Passed: zero errors, warnings, or hints. |
| `npm run build --prefix docs` | Passed: 63 pages. |
| `npm run check:links --prefix docs` | Passed. |
| `git diff --check` | Passed. |

The site build emits an `Entry docs → 404 was not found` notice while generating
its 404 route; the build and link validation succeed. Intermediate typing and
documentation-test failures were corrected. No required checks remain outstanding,
and no wheel or package-distribution checks were performed.

The upstream Pydantic limitation for literal dictionary defaults containing a
`$ref` key remains documented in the authoring guide and covered by the existing
regression test. No additional workaround was added.
