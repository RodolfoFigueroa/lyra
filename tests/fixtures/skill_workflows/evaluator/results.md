# Initial skill evaluation — 2026-09-25

## Method

Use the prompts and inputs in `../scenarios/README.md` and the staged answers in
this directory's README to repeat these trials. Each trial used a fresh agent
and an isolated project under `/tmp/lyra-metric-trials/`. Agents received only
their workflow, a copy of the skill, package/metric naming, and access to the
existing Python environment. They were instructed not to inspect other trial
inputs or the evaluator rubric. Existing-plugin input also included a copy of
the canonical example package.

The evaluator supplied answers only after observing clarification questions.
Runtime dependency declarations were authorized; package installation, wheel
checks, network operations, publishing, and source-repository edits were excluded.
The environment contained lyra-sdk 0.14.0. Commands used
`uv run --project /home/lain/Documents/lyra --no-sync` from each scratch project.
Those local paths describe this run, not requirements for installing the skill.

The evaluator reviewed questions and generated adapters, packaging metadata,
manifests, and tests. Generated plugins remain scratch artifacts, not new public
examples or maintained implementations. The checked-in reference example has
separate automated regression coverage in `tests/test_metric_skill.py`.

## Observed outcomes

| Scenario | Observed behavior | Local validation |
| --- | --- | --- |
| New plugin | Used the documented semantics without clarification; packaged the unchanged calculation, preserved feature identity and selection behavior. | 21 generated tests passed; format, lint, type and manifest checks passed. |
| Existing plugin | Added zone_score while preserving package identity, all three previous registrations and their declarations, and table/file/progress behavior. Packaged the unchanged calculation and declared its direct dependencies. | 13 generated tests passed; format, lint, type and manifest checks passed. |
| Missing information | Asked about output meaning/units, value type/nullability/missing behavior, CRS, geometry types, applicability and source of properties. Also noticed the evaluator had requested a nonexistent callable name. No dependent implementation preceded the answer. Completed after the evaluator confirmed the callable and supplied the contract. | 8 generated tests passed; format, lint, type and manifest checks passed. |
| Dynamic columns | Identified request-dependent columns as incompatible with a static table contract and asked the evaluator to choose an adaptation. Implemented fixed urban/rural columns only after that choice, preserving the original calculation. | 7 generated tests passed; format, lint, type and manifest checks passed. |
| Ambiguous rows | Asked for the source and feature identity mapping of upstream scores; noted that GeoJSON conversion cannot supply the undocumented DataFrame attrs. After the evaluator confirmed no reliable mapping exists, stopped and explained the necessary upstream change. | Confirmed SDK version and demonstrated differing list/dictionary indexing. No plugin or manifest was created; implementation checks were correctly inapplicable. |
| File report | Used the documented format and feature order; called the original workflow with a path inside the job temporary directory. Included the original workflow in package configuration. | 6 generated tests passed, including byte equivalence with Unicode IDs, outside paths, symlink escapes, missing files and suffix mismatch. Format, lint, type and manifest checks passed. |

For the missing-information trial, the evaluator additionally specified that
missing values fail without filling and that fractional baseline indices require
a numeric output. For the dynamic-column trial, the answer established one
nonnullable integer indicator per fixed category, with missing category properties
rejected. These were evaluator-provided decisions, not inferred defaults.

## Improvements from trials

- The new-plugin trial initially used the wrong `MetricInputError` constructor;
  its validation caught the error and the agent corrected it. The reference now
  documents the required `(metric, path, message)` arguments.
- The missing-information and dynamic-column trials initially tried parameter
  preparation for parameterless metrics. The reference and skill now explicitly
  skip this helper when there is no parameter model. Subsequent trials received
  the corrected guidance.
- File-report metadata initially declared Python >=3.12 while tests used 3.11.15.
  Metadata review established that the SDK requires >=3.11; the trial corrected
  its declaration and rechecked the manifest. The reference now requires the
  declared Python range to match project/dependency evidence and include the
  verification interpreter. Packaging inclusion was inspected statically, not
  by building or installing a wheel.

These are observations from controlled examples. They do not establish scientific
validity for real indicators or guarantee that every agent will follow the skill.

## Repository verification

- Skill-creator structural validator: passed. This was a development check;
  neither the distributed skill nor its installation guide depends on it.
- `uv run ruff format .`: 177 files unchanged.
- `uv run ruff check .` and `uv run ty check`: passed.
- `uv run pytest tests/test_metric_skill.py tests/test_docs_contract.py`: 27 passed.
- `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`
  with sandbox escalation: 716 passed, 28 skipped; 87% total coverage and
  `coverage.xml` generated. The existing Redis integration tests require
  `LYRA_TEST_REDIS_URL` pointing to a disposable service and were not exercised.
- After the reference clarifications, the 12 skill tests and structural validator
  passed again.
- Documentation: `npm ci --prefix docs`, followed by `npm run generate`,
  `npm run check`, `npm run build`, and `npm run check:links` with `--prefix docs`,
  all passed. Astro checking reported zero errors, warnings, or hints.
- `git diff --check`: passed.

No application or SDK code, dependency files, or lint/type/test rules were changed.
The documentation install reported eight vulnerabilities in the unchanged
dependency lockfile (one moderate, six high, one critical); dependency remediation
is outside this change. The successful documentation build also emitted the
existing missing custom `404` entry message. No wheel smoke checks were performed.
