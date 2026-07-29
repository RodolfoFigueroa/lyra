# Step 01: PostGIS integration harness

Status: `complete`

Depends on: none

## Objective

Add a small, deterministic PostGIS integration-test environment that exercises
the current database implementation before it is moved or hardened. This step
must add coverage without changing production database behavior.

## Current state

Database lifecycle tests in `tests/test_database_runtime.py` use fake engines and
connections. `lyra_app/db/client.py` and `lyra_app/loaders/db.py` contain the
real GeoPandas/PostGIS queries, but CI does not start PostGIS and the query
results are not covered against a real database.

The production database is supplied externally and is not created by Lyra.
Integration tests therefore need their own minimal schema and data; they must
not depend on a production dump.

## Required implementation

### Test execution boundary

- Add an integration-test marker for tests requiring PostgreSQL/PostGIS.
- Tests must obtain their database URL from a dedicated test-only environment
  variable. They must never fall back to the normal Lyra production
  configuration variables.
- Local test runs may skip the integration suite when the test URL is absent.
  CI must provide the URL and must fail, rather than skip, if its configured
  database service is unavailable.
- Add a pinned PostGIS service to the Python CI job. Keep its credentials and
  database disposable and test-only.
- Do not add Testcontainers or another orchestration dependency; use the CI
  service facility and ordinary SQLAlchemy/Psycopg setup.

### Fixture schema

Create the smallest schema needed to exercise every current public `LyraDB`
method:

- one supported DENUE table with its expected value and geometry columns;
- one supported mesh-level table;
- one supported census-level table with at least one selectable census column;
- PostGIS enabled;
- geometries in SRID 6372 that give deterministic intersecting and
  non-intersecting rows for known bounds.

Fixture creation and teardown must be isolated from other tests. Prefer a
dedicated schema or disposable database and deterministic SQL fixtures checked
into the repository. Tests may use administrative fixture credentials to create
objects, but production code must connect through the ordinary application
adapter.

### Characterization coverage

Exercise:

- `load_denue_from_bounds`;
- `load_mesh_from_bounds`;
- `load_census_from_bounds`;
- intersection and non-intersection behavior;
- returned column names and ordering;
- geometry column presence, geometry values, and CRS;
- repeated calls through a pooled engine;
- connection return after success and after a database exception.

The tests should describe behavior that downstream plugin code actually relies
on. Do not encode incidental formatting of generated SQL.

### CI and local guidance

Document how a developer can start or point to a disposable PostGIS instance
and run only the integration tests. The normal unit suite must remain fast and
must not unexpectedly connect to a database.

## Expected files

Likely areas include:

- `.github/workflows/ci.yml`;
- a new integration-test module or package under `tests/`;
- test-only SQL or Python fixture support;
- pytest marker configuration if the project requires explicit registration;
- contributor-facing test documentation.

Do not edit the production query implementation in this step.

## Acceptance criteria

- CI executes real PostGIS tests on every Python validation run.
- A normal local unit-test run without the test URL performs no database
  connection and reports integration tests as skipped.
- Every current `LyraDB` method is exercised against real PostGIS data.
- Test fixtures are deterministic, isolated, and contain no production data.
- No production configuration, readiness behavior, query construction, or
  error mapping changes.

## Verification

Run the repository Python checks and:

- the integration suite against the disposable PostGIS instance;
- the same integration selection without the test URL, confirming safe skips;
- the existing database runtime tests;
- the full suite with coverage because CI configuration and global test
  collection changed.

## Non-goals

- No database schema-version or compatibility probe.
- No read-only role enforcement yet.
- No query hardening or behavior corrections.
- No SDK packaging changes.
- No production Docker Compose PostGIS service.

## Implementation record

Complete this section when the step is implemented.

- Date: 2026-07-28
- Material changes: Registered the `integration` pytest marker; added an
  environment-gated PostGIS characterization suite and deterministic SQL
  fixture in an isolated schema; covered all current `LyraDBImplicit` spatial
  methods, intersecting and empty results, result columns/geometries/CRS,
  repeated pooled calls, and connection return after success and database
  errors; added a digest-pinned PostGIS 17 / PostGIS 3.5 service to Python CI;
  and documented disposable local execution in `CONTRIBUTING.md`.
- Verification: `uv run ruff format .` passed; `uv run ruff check .` passed;
  `uv run ty check` passed; integration selection without
  `LYRA_TEST_POSTGIS_URL` reported 5 skipped and made no database connection;
  `uv run pytest tests/test_database_runtime.py -vv -s` passed (6 tests);
  `uv run pytest -m integration -q` against the disposable PostGIS service
  passed (5 tests); and the full coverage command passed (710 tests, 86% total
  coverage) and generated `coverage.xml`.
- Deviations or follow-up notes: None. Production database behavior and later
  handoff assumptions were unchanged.
