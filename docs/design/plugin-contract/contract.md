# Plugin contract redesign

Status: **SDK, application, documentation, and final verification complete**.
This is an internal design document, not documentation for the released SDK.
See the handoff for completed integration checks and final verification results.

Related documents: [complete examples](examples.md) and
[implementation handoff](handoff.md).

## Objective and boundaries

Expose existing Python computations through a small adapter. Authors declare
ordinary parameters once, return native computation results, and can test their
adapter without an API, Redis, Celery, PostGIS, or Earth Engine connection.

The application is in closed alpha. Replace the old contract outright: no
migrations, compatibility readers, aliases for old classes, or runtime shims.
The implementation replaces the contract on one coordinated feature branch;
merge only after integration and final verification.

The agreed simplifications are:

- Ordinary lists with fixed output columns. Remove keyed batches, batch labels,
  unique-key rules, and request-dependent output column templates.
- One generated manifest, with the complete public request JSON Schema. Remove
  semantic ordinary-input models and the authoring/compiled-manifest split.
- One parameter-model authoring style. Remove the decorator's `inputs` mapping
  and the `Input`, `BatchInput`, and `BatchItem` authoring interfaces.
- Native DataFrame and `Path` returns. Plugin authors do not construct job
  envelopes, attach job IDs, or return transport result models.

Retain source capture, explicit plugin factories, source-integrity checks,
manifest-only API discovery, table/file declarations, server-derived area
fractions, spatial resolution, cancellation, progress snapshots, and provenance.
Do not change the database-interface generator, queues, installation workflow,
authentication, result retention, or job observation design.

## Public authoring interface

Expose these names from `lyra.sdk`:

| Name | Contract |
| --- | --- |
| `metric` | Decorator accepting keyword-only `name`, `description`, and `output`. Records a definition and returns the original function. |
| `PluginDefinition` | Explicit collection constructed with `metrics=[...]`; no module scanning. |
| `MetricParameters` | Convenient Pydantic base with `extra="forbid"` and `validate_default=True`; no global strict/coercion override. |
| `LocationInput` | Runtime annotation for the existing multi-feature `GeoJSON` model. |
| `BoundsInput` | Runtime annotation for the existing `SingleGeoJSON` model. |
| `RunContext` | Existing database, temporary-directory, logging, progress, and cancellation services. |
| `TableOutput`, `TableColumn`, `FileOutput` | Unversioned output declarations described below. |
| `FractionOfLocationArea` | Existing server-owned area-fraction declaration, under an unversioned name. |
| `PluginDefinitionError` | Invalid authoring definition or unsupported schema capability. |
| `MetricInputError`, `MetricResultError` | Typed errors from the shared execution preparation and result validation operations. |

Manifest models live in `lyra.sdk.models.plugin`: `PluginInfo`, `MetricManifest`,
and `PluginManifest`. Keep ordinary job/geometry models in their existing owning
modules. Format versions belong in data, not Python class or module names.

### Handler conventions

Handlers are synchronous functions. Supported argument names are `parameters`,
`location`, `bounds`, and `context`; their order is irrelevant because the worker
calls by keyword. Positional-or-keyword and keyword-only arguments are accepted.
Reject positional-only arguments, variadic arguments, asynchronous/generator
handlers, unsupported names, missing annotations, and handler argument defaults.

- `parameters`, when present, is annotated with a concrete Pydantic `BaseModel`
  subclass. Its fields own all ordinary defaults. Do not repeat the model in the
  decorator. A metric with no ordinary inputs omits this argument entirely.
- Require at least one of `location: LocationInput` or `bounds: BoundsInput`.
  Both may be present, but neither can be nullable or have a default. A table
  metric must have `location` so its rows have authoritative geographic identity.
- `context: RunContext` is optional in the signature. The worker supplies it
  only when declared. File metrics normally declare it to use `context.temp_dir`.
- Return annotations may be omitted. If supplied, they document/type-check the
  native return; the explicit output declaration remains authoritative at runtime.

Preserve the existing lowercase public-name rule and reserved `lyra_` prefix for
metric identifiers. Do not invent an additional naming DSL for fields inside a
parameter model: use Pydantic field names, with aliases excluded as described
below. Duplicate metric names and empty descriptions fail registration.

Direct calls are ordinary Python calls. They receive already constructed
parameters and SDK geometry and return the native object. The decorator performs
registration checks but does not connect to services, resolve references,
normalize results, or inject context when the developer calls the function.

### Parameter models and supported values

Allow existing Pydantic models when compatible. Do not clone or mutate them to
change their behavior. Require `extra="forbid"` and `validate_default=True` on
the root model and every nested Pydantic object model, including models inside
containers/unions. Developers can use a small subclass and replace incompatible
nested model annotations. Inherited configuration counts.

Typed `dict[str, T]` fields remain deliberately open mappings; they are not
unknown model fields. The root parameters value is always a model/object.

Initially support `str`, `bool`, `int`, finite `float`, `None`, homogeneous
JSON-scalar `Literal` enums, lists, string-keyed dictionaries, unions of supported
types, and nested models. Accept native `Field` constraints and descriptions,
examples, and Pydantic validators. Parameter field descriptions are required for
root fields; nested descriptions and examples are encouraged but optional.

Reject `Any`, arbitrary Python classes, root models, recursive models, unresolved
generic models, sets, tuples, bytes, datetimes, decimals, non-string dictionary
keys, custom serializers, computed fields, field aliases, default factories, and
custom JSON Schema hooks/structural schema overrides in this first contract.
These exclusions bound schema/runtime differences and are registration errors,
not silent conversions. Error messages identify the metric and field path.

Do not add a second schema language to describe Pydantic validators. Python-only
validators run in the worker and may reject schema-valid input. Validators must
preserve declared value types; authors remain responsible for semantic behavior
that cannot be proved by inspecting the schema. Prefer model-level after
validators for cross-field checks. A published schema promises structural
validation, not successful execution of every domain rule.

### Defaults, examples, and coercion

- Model fields alone define omission, defaults, and nullability. A nullable field
  without a default is still required.
- Defaults are deterministic, JSON-serializable values; mutable literal defaults
  follow Pydantic's instance-copying behavior. Factories are unsupported.
- Manifest generation checks field defaults/examples against the generated field
  schema with its local definitions. It does not execute arbitrary semantic
  validators on incomplete example objects. Full-object examples, when provided,
  are checked against the complete parameter schema.
- The worker parses the submitted JSON through the model and validates default
  values using the required `validate_default=True` configuration. Invalid
  semantic defaults fail the job just like other worker-only input errors.
- API JSON Schema validation does not coerce values: a numeric string is not an
  integer. Use normal Pydantic JSON parsing after schema validation, rather than
  globally forcing strict mode. JSON integers such as `1.0` satisfy the schema's
  integer rule; model-specific stricter checks may reject them in the worker.
- Preserve the submitted unresolved request, without injected defaults, in
  provenance and idempotency hashing. Omitted defaults and explicitly supplied
  defaults can therefore yield different fingerprints. Do not add effective
  parameter recording in this redesign.

## Public requests, manifest, and runtime flow

Keep the job submission envelope (`metric`, `input`, optional `idempotency_key`).
Within `input`, spatial references are siblings of `parameters`.

If a handler declares `parameters`, that property is required even when all model
fields have defaults; `{}` is then sufficient. If the handler omits `parameters`,
the property is absent from its schema and sending it is an unknown-field error.
All declared spatial fields are required. The request root rejects extra fields.

Preserve the existing spatial reference unions: explicit GeoJSON, same-level
CVEGEO lists, and metropolitan-zone codes. API spatial resolution validates rules
not expressible in the schema, such as uniform CVEGEO lengths, before queuing.
Workers receive geometry models, never unresolved wrappers.

### Manifest format 5

The persisted object has exactly these fields:

| Object | Fields |
| --- | --- |
| Plugin manifest | `schema_version: 5`, `plugin`, `factory`, `metrics` |
| Plugin identity | Nonempty `name`, `version` |
| Metric manifest | `name`, `description`, `request_schema`, `spatial_inputs`, `output` |
| Spatial metadata | Mapping of declared canonical names to `"location"` or `"bounds"` |

Metrics are nonempty and names unique. Reject extra manifest fields. Factory
syntax remains `module:attribute`; the factory is synchronous and parameterless.
The generated manifest includes no queues, job state, handler signature,
parameter-model import path, separate parameter schema, or compiled representation.

Generate the complete public request schema in one Pydantic schema-generation
pass, using a temporary root model containing the parameter model and Lyra's
spatial-reference types. This lets Pydantic own definition naming and references;
do not generate field schemas separately and hand-hoist/rewrite their `$defs`.
Declare Draft 2020-12 explicitly. Permit `const` and internal discriminator
annotations; remove the `const`-to-`enum` transformation. References must resolve
within the document using `#/$defs/...`; never fetch remote schemas.

Write deterministic JSON: sort metrics by name, sort object keys when writing,
preserve meaningful array order (including columns), indent by two spaces, and
end with one newline. Preserve generated titles/descriptions and the standard
JSON Schema dialect URL. Same-definition generation in the same dependency
environment must be repeatable. Changed schema-generator output after a dependency
upgrade requires regenerating the manifest; no semantic equivalence engine.

`PluginDefinition.manifest(plugin=PluginInfo(...), factory=...)` returns this one
contract. Remove `compiled_manifest`. `describe(name)` exposes name, description,
handler reference/signature, request schema, spatial metadata, and output;
inspection-only fields are not persisted in the manifest. Retain CLI `describe`,
`build-manifest`, `check-manifest`, and pre-commit integration. CLI descriptions
read the canonical schema/metadata; an unfamiliar schema construct can be shown
as JSON instead of introducing a reverse compiler.

The API checks the manifest and schema without importing the plugin. It builds
its validator directly from `request_schema`. The worker imports the explicit
factory, regenerates the manifest with the captured plugin identity, and compares
parsed manifest objects. Any difference prevents worker readiness. Version 4
manifests are unsupported after the implementation is complete.

### Shared local and worker operations

Define two operations on `PluginDefinition` so local tests reuse worker behavior:

- `prepare_parameters(name, value)` accepts an ordinary JSON object, validates
  the parameter subschema with the root definitions, then constructs the declared
  Pydantic model using JSON parsing. It returns the model or raises
  `MetricInputError` with a field path. Calling this for a parameterless metric is
  a definition-usage error. It does not resolve spatial references or access services.
- `normalize_result(name, value, *, job_id, location=None, temp_dir=None,
  location_areas_m2=None)` validates the native output and produces the existing
  typed terminal success result. It raises `MetricResultError` on failure.
  `location` is resolved `GeoJSON`, `temp_dir` is a `Path`, and area metadata is a
  feature-ID-to-square-metres mapping. Require the applicable arguments for the
  declared output; no database queries or global configuration lookups.

Use a narrow DataFrame-like protocol for structural extraction, preserving the
SDK's current pandas-free dependency set. No new SDK pandas/geospatial runtime
dependency is authorized. Plugin projects declare pandas/geopandas themselves.
Normalizers read `index`, `columns`, and each selected column's `tolist()` result;
extract columns independently so a mixed integer/float frame does not promote
integer cells to floats through a whole-frame NumPy conversion. Other arbitrary
return objects are unsupported. Do not add Series, dictionaries,
raw terminal JSON, or legacy job-bearing results as alternative success APIs.

The worker uses these same operations around its handler invocation. Preparation
failures produce `failed` jobs with error type `invalid_input`; normalization
failures use `invalid_result`. Keep existing exception handling for unexpected
execution failures and cooperative cancellation. Authors raise exceptions for
domain failures; the new handler contract does not return `FailedJobResult`.

## Output contract and normalization

`TableOutput(columns=[...])` has an implicit/default `kind="table"` discriminator.
Require at least one static column. `TableColumn` has `name`, `type` (integer,
number, string, boolean), `unit`, `description`, `nullable=False`, and optional
`derivations=[]`. Preserve current derivation naming/collision checks and the
single area-fraction derivation per source column. There is no `batched_columns`.
Manifest output declarations always include `kind` and column `nullable` values;
omit empty `derivations` arrays.

For DataFrame normalization:

- Read rows/columns in their existing order. Require a one-dimensional index of
  strings matching resolved location feature IDs exactly, with no duplicates.
  Require exact string column names/order matching the declared source columns.
  Do not stringify indices, infer an identity column, or silently align rows.
- Convert numerical array scalar wrappers to their equivalent Python scalar
  values where necessary. This is representation normalization, not conversion
  between declared scalar types; booleans are not integers and floats are not
  integer cells, even when integral.
- Normalize `None` and IEEE NaN to JSON null; null requires a nullable column.
  Reject positive/negative infinity. Other missing sentinels, including pandas
  `NA`/`NaT`, require explicit conversion by the adapter in the initial contract.
  Do not import pandas merely to detect them.
- Reject unsupported cell objects, nested values, timestamps, complex values,
  and dtype-driven rounding/truncation. Report row and column for invalid cells.
- Validate the native table, then apply declared server-derived columns. Preserve
  canonical Mexico area computation, source-unit `m2` requirements, feature-ID
  alignment, and the existing fraction tolerance/clamping rule (tolerance `1e-9`).
  The shared normalizer consumes areas supplied by the caller; it does not compute
  or fetch them. Validate derived values before returning the terminal result.

`FileOutput(media_type=..., extensions=[...])` has implicit/default `kind="file"`.
The handler returns a `Path`; media type comes from the declaration. Resolve
relative paths under the job's temporary directory. Require an existing regular
file whose resolved path is inside that directory, including after symlink
resolution, and whose final suffix matches a declared extension case-insensitively.
Preserve the current extension validation. No MIME sniffing or file copying.

The platform supplies job ID, status, provenance, retention, and download access.
Local tests can supply a synthetic job ID and a temporary directory to the shared
normalizer. Direct handler calls continue to return the native DataFrame/Path.

## MCP and operational integration

Publish the full request schema through catalog/metric inspection. Keep the
current MCP metropolitan-zone execution interface: it supplies its `parameters`
argument under `input.parameters`, not flattened into the spatial root. For a
parameterless metric, omit that property when MCP receives its default empty
parameters; reject nonempty parameters instead of discarding them.

MCP's current execution helper supports exactly one spatial input. Retain that
limit: location-and-bounds metrics are discoverable but executed through REST or
the Python client. Return the existing unsupported-spatial-shape tool error for
an attempted dual-spatial MCP run. Do not expand MCP's tool surface in this change.

Keep administrative CLI source/routing/worker operations intact. Update catalog
rendering and plugin CLI descriptions where the new contract changes their data.
No plugin source, queue, deployment, or database-interface redesign is included.

## Completion criteria

The [examples](examples.md) define the six executable acceptance specimens in
`tests/fixtures/contract_plugin/`. Completion requires their SDK and application
integration checks to pass, published guides and public exports to match this
contract, obsolete authoring interfaces to be removed, and the [handoff](handoff.md)
to record final Python and documentation verification. No product decisions remain
open. The runnable public example is `examples/lyra-plugin/`.
