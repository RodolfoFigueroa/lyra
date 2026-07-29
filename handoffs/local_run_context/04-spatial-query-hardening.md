# Step 04: Harden spatial query construction

Status: `planned`

Depends on: [Step 03: Extract the PostgreSQL adapter into the SDK](03-postgres-adapter-extraction.md)

## Objective

Make every Lyra-owned PostGIS query safe under direct SDK use, explicit about
its schema, runtime-validated, and deterministic where ordering is observable.
This covers both `PostgresLyraDB` and the application-only spatial-resolution
loaders.

## Current state

The existing loader layer binds ordinary values, but composes table and column
identifiers through formatted SQL strings. `quoted_name` metadata does not quote
an identifier after it is converted to a plain f-string. Several identifier-like
arguments are protected only by static `Literal` annotations, which provide no
runtime validation to a direct Python caller.

Application spatial-resolution queries also use unqualified table names and
some result sets have no defined ordering. Step 03 deliberately preserved this
behavior to isolate the ownership move.

## Required implementation

### Explicit schema

- Add an explicit database data-schema setting to application configuration,
  defaulting to the schema used by existing deployments.
- Validate it as a supported PostgreSQL identifier and include it in generated
  configuration documentation and deterministic config rendering.
- `PostgresLyraDB` accepts the schema explicitly, with a backward-compatible
  default suitable for independent SDK use.
- Pass the configured schema into worker adapters and application spatial
  converters/loaders.
- Qualify all Lyra data tables. Do not rely on ambient `search_path`.

This is schema selection, not schema-version negotiation. Do not add a metadata
table or compatibility probe.

### Identifier composition

- Replace formatted caller-controlled identifiers with one dialect-aware
  composition approach shared by the affected queries.
- Prefer SQLAlchemy expression constructs where they interoperate cleanly with
  GeoPandas. A small centralized identifier compiler is acceptable when
  GeoPandas requires textual SQL.
- Never treat identifiers as value parameters, and never interpolate unvalidated
  caller strings.
- Keep bounds, codes, names, and other data values bound as parameters.
- Ensure every geometry expression selects a geometry column in the form
  expected by `geopandas.read_postgis`.

### Runtime validation

Validate at the concrete adapter boundary even when type annotations use
`Literal`:

- DENUE year and month;
- mesh level;
- census level;
- requested census columns;
- bounds finiteness and minimum/maximum ordering;
- non-empty, same-level CVEGEO collections in application loaders;
- identifier length and emptiness.

Column names must be non-empty, unique, within PostgreSQL identifier length,
and follow the project's supported simple-identifier policy. Handle
`geometry` exactly once. Reject invalid calls with `ValueError` before opening
a database connection.

### Deterministic semantics

- Preserve caller CVEGEO order when resolving a list.
- Define stable secondary ordering where a query can otherwise return equal
  rows unpredictably.
- Preserve existing public column order, including automatic geometry
  inclusion.
- Correct loader-level invalid-length behavior rather than producing a table
  name containing `None`.

### Test expansion

Extend Step 01 fixtures as needed to cover:

- schema qualification;
- every runtime validation branch;
- malicious, reserved, blank, duplicate, and overly long identifiers;
- deterministic CVEGEO result order;
- valid parameter binding;
- expected CRS and output columns after the query rewrite.

Tests should assert results and rejection behavior, not exact SQL formatting.

## Compatibility requirements

- Valid existing plugin calls produce the same GeoDataFrames.
- Existing deployment configuration works through the default schema.
- Invalid calls may now fail earlier and more clearly.
- No public method gains a raw SQL escape hatch.
- No database reflection is performed on every call merely to validate column
  names; avoid adding avoidable round trips.

## Expected files

Likely areas include:

- optional SDK PostgreSQL modules from Step 03;
- `lyra_app/loaders/db.py`;
- `lyra_app/converters/`;
- `lyra_app/config.py`;
- `config.example.toml`;
- generated config/reference documentation;
- integration and unit tests.

## Acceptance criteria

- No caller-controlled identifier is interpolated into raw SQL.
- All Lyra data tables are schema-qualified.
- Direct Python calls receive runtime validation equivalent to their type
  contracts.
- CVEGEO resolution order is deterministic.
- Valid Step 01 database behavior remains intact.
- No contract-version or readiness behavior is introduced.

## Verification

Run the required Python checks, plus:

- expanded PostGIS integration tests;
- configuration contract, loader, spatial-input, and worker tests;
- documentation generation/checks affected by the new schema setting;
- the full suite with coverage due to changes across SDK, application, and
  configuration.

## Non-goals

- No schema-version metadata, compatibility negotiation, or migration system.
- No row limits, streaming, or query API redesign.
- No connection-policy or error-taxonomy changes.
- No local context yet.

## Implementation record

Complete this section when the step is implemented.

- Date:
- Material changes:
- Verification:
- Deviations or follow-up notes:
