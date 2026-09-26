# Platform-contract follow-up evaluation — 2026-09-25

This follow-up checks the revised clarification rules and polygon-only development
contract after the original 0.14.0 baseline. The editable SDK still reports 0.14.0;
its inspected location/bounds schemas now exclude Point. The earlier results in
`results.md` describe the initial skill and have not been rewritten.

## Fresh-agent trials

Two fresh agents received isolated copies of the updated skill and one workflow
each under `/tmp/lyra-platform-trials/`. They received the corresponding scenario
prompt, package/metric names, permission to declare dependencies, and access to the
existing development environment. They were prohibited from reading evaluator
files or other trials, installing packages, building wheels, or making network or
live Earth Engine calls. The evaluator inspected their questions and generated
adapters, packaging metadata, and tests.

| Scenario | Observed behavior | Outcome |
| --- | --- | --- |
| Documented Earth Engine workflow | Asked no questions about platform authentication, input geometry types, CRS, or coverage. Preserved internal EPSG:4326 reprojection, source band, 30-metre reduction, nullable metre-valued output, and IDs/order. Reported geographic applicability as unverified without blocking implementation. | Completed; 2 offline tests passed, 100% plugin coverage. Format, lint, type, manifest generation/inspection/freshness and unchanged-workflow checks passed. |
| Ambiguous Earth Engine output | Asked only what the private source band's values represent and what unit to declare. Explicitly distinguished that blocker from already established reprojection, resolution, null handling and nonblocking geographic uncertainty. Waited before implementing the adapter/manifest. | After the staged answer (synthetic dimensionless index, nullable mean, no asserted range), completed; 2 offline tests and format, lint, type and manifest checks passed. |

Both generated adapters simply convert resolved geometry and call the existing
calculation. Neither initializes/authenticates Earth Engine, imports `lyra_app`,
discovers credentials, adds project-ID parameters, or restricts incoming CRS to
EPSG:4326. Tests use projected inputs and distinct, nonlexical feature IDs, compare
original/adapted values including missing results, and reject row/column mismatch
and infinity. Import/factory/manifest checks forbid Earth Engine initialization,
authentication, and object construction. Packaged source calculations remain
unchanged. Trial artifacts remain temporary, not additional supported examples.

Commands ran from each scratch project using
`uv run --project /home/lain/Documents/lyra --no-sync` with `ruff format .`,
`ruff check .`, `ty check`, focused pytest, and `lyra-plugin build-manifest`,
`describe`, and `check-manifest`. Initial exploratory import mistakes and a test
CRS-nullability finding were corrected before the final passing checks. These
paths record the local evaluation environment, not installation requirements.

No further skill changes were necessary based on these trials. They provide
behavioral evidence, not a guarantee of agent compliance or scientific validity.
Live dataset access, permissions, coverage, and real-data results were not tested.

## Repository checks

- `uv run ruff format .`: passed, 183 files unchanged.
- `uv run ruff check .` and `uv run ty check`: passed repository-wide.
- Focused pytest covering spatial models/schemas, converters, API submission,
  SDK invocation/results, geometry, worker startup, skills and documentation:
  122 passed. Spatial runtime tests ran with sandbox escalation.
- `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`:
  744 passed, 28 skipped, 87% total coverage, with `coverage.xml` generated.
  Ran with sandbox escalation. Skips are the existing Redis integration tests
  requiring `LYRA_TEST_REDIS_URL` and a disposable Redis service.
- Example `lyra-plugin build-manifest` and `check-manifest`: passed. The checked-in
  manifest was generated from the updated SDK, not edited manually.
- Skill structural validation and `git diff --check`: passed.
- Documentation generation, Astro checking, build, and link checking: passed
  (`npm run generate/check/build/check:links --prefix docs`). Astro reported no
  errors, warnings, or hints. The build still emits the existing missing custom
  `404` entry message.
- `npm ci --prefix docs` initially failed with `esbuild` subprocess `EPERM` in the
  sandbox. Retrying with escalation succeeded. The unchanged documentation
  dependency lockfile still reports eight vulnerabilities (one moderate, six
  high, one critical); dependency remediation is outside this change.

No test/lint/type rules or dependency declarations were weakened or changed.
No wheel verification or live Earth Engine requests were performed. Installed
personal skill copies were not modified; refresh them from the updated source.
