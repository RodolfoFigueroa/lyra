# Lyra authoring contract

This reference defines **lyra-authoring-2**, the bundled authoring baseline,
and **manifest format 5**. Use it to learn the public contract. Run the bundled
compatibility helper in the target environment to check spatial and authoring
behavior; its SDK version report is diagnostic, not a release allowlist.
The baseline revision identifies this reference and its checks, not an SDK or
application release. Passing checks do not certify every SDK behavior, scientific
correctness, or deployment readiness. Earth Engine initialization below is an
application responsibility and cannot be established by the SDK helper.
This reference is bundled so an external workflow repository can be adapted
without a Lyra checkout.

## Parameters and handlers

A metric is a synchronous function decorated with `lyra.sdk.metric`. Accepted
argument names are `parameters`, `location`, `bounds`, and `context`; the worker
calls by keyword. Positional-only arguments, variadics, handler argument defaults,
async handlers, and generators are rejected.

- `parameters` is a concrete Pydantic model. `MetricParameters` supplies
  `extra="forbid"` and `validate_default=True`; nested models need those settings
  too. Omit this argument for a metric without ordinary inputs.
- Root parameter fields require descriptions. Preserve evidenced defaults and
  constraints using `Field`; omission and accepting `None` are different choices.
- Supported parameter values include strings, booleans, integers, finite floats,
  null, homogeneous scalar Literal enums, lists, string-keyed dictionaries,
  unions, and nested models. Do not use aliases, `Any`, sets, tuples, datetimes,
  recursive models, default factories, custom serializers, computed fields, or
  custom schema overrides. Consult version-matched documentation for other types.
- `location: LocationInput` receives one or more Polygon/MultiPolygon GeoJSON
  features, with string IDs and a declared CRS. `bounds: BoundsInput` receives
  exactly one Polygon feature. Every metric needs at least one spatial argument;
  tables require `location`. Bounds need not be rectangular.
- Optional `context: RunContext` supplies a database client, logger, temporary
  directory, and `report_progress(...)`. Declare it only when needed. Spatial
  arguments cannot be nullable or defaulted.

`convert_geojson_to_gdf` from `lyra.utils.geometry` preserves the declared CRS,
feature properties, order, and feature IDs as the GeoDataFrame index. It does not
reproject or create workflow-specific attributes. Resolved administrative zones
are not guaranteed to contain arbitrary attributes required by a calculation.
Inputs are not universally EPSG:4326. Preserve reprojection already performed by
the calculation; do not restrict the incoming CRS to its internal working CRS.
The platform's region selection does not establish a dataset's geographic
coverage or a metric's scientific applicability. Distinguish region size or
administrative level from raster reduction resolution. Report undocumented
applicability without claiming unlimited validity or inventing a restriction.

Metric names match `^[a-z][a-z0-9_]*$` and cannot start with `lyra_`.

## Descriptions for independent consumers

Treat manifest metadata as the consumer-facing explanation of a metric. Consumers
such as MCP clients do not have the plugin README. Transfer relevant methodology
from the workflow's documentation, implementation, tests, and user answers into
the descriptions; keep claims traceable to that evidence.

Start the metric description with a useful summary sentence identifying the
quantity and operation. Follow it with proportionate detail about source datasets
and versions when known, aggregation, consequential thresholds, temporal meaning,
reduction resolution, missing-data behavior, and documented limitations. Include
only applicable details: a property-based score need not have a raster resolution
or external dataset. Distinguish computational resolution from scientific spatial
validity, and measured quantities from proxies or estimates.

Distribute the explanation without unnecessary duplication:

- Metric descriptions explain overall methodology, interpretation, and limitations.
- Parameter descriptions explain choices, units or scales, temporal conventions,
  and their effects on the calculation.
- Column descriptions explain individual outputs, workflow-defined score scales,
  and what zero or null means when relevant. `nullable=True` alone does not explain
  why a result can be missing.

Use readable sentences and paragraphs. Source identifiers and links support the
explanation but do not replace essential interpretation. Leave repository setup,
installation, and unrelated implementation details in repository documentation.
MCP catalog listings abbreviate descriptions; individual metric inspection exposes
the complete metadata, so put the useful summary first. There is no required
paragraph count or minimum length.

Do not invent methodology, scientific validity, or missing-data semantics to fill
out a description. Ask about material ambiguity or conflicting evidence before
finalizing dependent metadata. Undocumented applicability can be reported as
unverified without inventing a restriction or automatically blocking authoring.
Review the generated metadata without the README: it should support selecting the
metric, supplying meaningful inputs, and interpreting the results.

For example, the documented offline Earth Engine evaluation workflow supports:
“Mean elevation per input polygon from the elevation band of USGS/SRTMGL1_003.
The calculation reprojects polygons to EPSG:4326 and applies a spatial mean at a
30-metre reduction resolution. Results are in metres; missing source pixels
produce null results, never zero. Geographic applicability limits have not been
established by this workflow.” Its output description can identify the nullable
mean elevation and its missing-data meaning. These facts belong to that fixture;
do not apply them to another workflow without evidence or interpret the offline
test as live data validation.

## Earth Engine lifecycle

The normal Lyra worker launcher initializes Earth Engine **before importing
plugin factories**. Deployment configuration supplies `earth_engine.project` and
`earth_engine.service_account_file`; Lyra reads the service-account credentials
and calls `ee.Initialize` with that project. A plugin author does not need to
choose a credential source or project ID for normal worker execution.

Handlers use the already initialized `ee` environment. Do not call
`ee.Authenticate` or `ee.Initialize` from a handler or factory, add credential or
project parameters to a metric, or import application configuration from
`lyra_app`. Access to a particular private asset is a separate deployment
requirement; platform initialization does not guarantee asset permissions.

Manifest generation imports plugins without starting Lyra. Importing `ee` is
allowed, but do not construct initialization-dependent images, collections,
reducers, or other objects at module scope or in the factory. Construct them
during calculation execution. Avoid Earth Engine expressions as function defaults
or parameter defaults, which execute during import.

Direct Python calls, SDK helpers, and standalone scripts do not initialize Earth
Engine. Offline tests should fake or mock the workflow's Earth Engine boundary;
make authentication and initialization fail if called during import or manifest
generation. An authorized live test or standalone script needs a separate setup
harness using its configured environment. Ask about local credentials only when
such a live execution is requested and the setup is unknown; ordinary plugin
authoring and offline validation do not require that question.

`lyra.utils.ee.reduce_ee_image_over_gdf` already reprojects a copy of the geometry
to EPSG:4326 and passes its `scale` argument as reduction resolution in metres.
Preserve an existing workflow's dataset, reducer, resolution, reprojection, and
feature mapping. These choices are evidence from the calculation, not new
platform defaults. Real computation may return unavailable values or require
asset access; establish handling from the workflow or ask if a decision is needed.

## Example: a documented existing calculation

This example assumes an existing `workflow.calculate_metric` whose documented
contract is: Polygon/MultiPolygon zones in EPSG:4326, numeric `base_value` and
string `category` properties, unique string index preserved in order; required
integer multiplier, float offset in score units, and list of selected categories;
one finite, nonnullable `score` column in score units. Selected rows compute
`base_value * parameter_1 + parameter_2`, others return `0.0`. No defaults or
additional parameter ranges are specified. These are example facts, not defaults
to apply to a user's calculation.

```python
import pandas as pd
from lyra.sdk import (
    LocationInput,
    MetricParameters,
    PluginDefinition,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)
from lyra.utils.geometry import convert_geojson_to_gdf
from pydantic import Field
from workflow import calculate_metric


class Parameters(MetricParameters):
    parameter_1: int = Field(
        description="Multiplier applied to each zone's base value."
    )
    parameter_2: float = Field(description="Additive offset in score units.")
    parameter_3: list[str] = Field(
        description="Category labels included in the calculation."
    )


@metric(
    name="zone_score",
    description=(
        "Compute a synthetic property-based score for each input zone.\n\n"
        "For categories selected by parameter_3, calculate base_value * "
        "parameter_1 + parameter_2; return zero for other categories. "
        "Each zone must supply a finite numeric base_value and string category. "
        "The workflow accepts EPSG:4326 polygons and multipolygons and uses "
        "their properties, not geometric measurements or external datasets.\n\n"
        "Return one finite, non-null score per input feature. This illustrative "
        "score has no established real-world interpretation or calibrated scale."
    ),
    output=TableOutput(
        columns=[
            TableColumn(
                name="score",
                type="number",
                unit=Unit.SCORE,
                description=(
                    "Synthetic score in workflow-defined score units: base_value * "
                    "parameter_1 + parameter_2 for selected categories, otherwise "
                    "zero. Zero is a computed result, not a missing-data marker; "
                    "selected categories can also compute to zero. No calibrated "
                    "range or real-world interpretation is established."
                ),
                nullable=False,
            )
        ]
    ),
)
def run(location: LocationInput, parameters: Parameters) -> pd.DataFrame:
    return calculate_metric(
        convert_geojson_to_gdf(location),
        parameter_1=parameters.parameter_1,
        parameter_2=parameters.parameter_2,
        parameter_3=parameters.parameter_3,
    )


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[run])
```

Keep the calculation in its existing module. Add this adapter to the project's
package, and point its factory configuration at the actual module. A plugin may
register several metrics explicitly; Lyra does not discover sibling modules.

## Native result contracts

For `TableOutput`, return a DataFrame with unique string index exactly matching
the location feature IDs **in order**, and source columns exactly matching the
declared names **in order**. Lyra does not align results for you. Align by an
established key only after checking for missing, extra, and duplicate identities;
never assign the input index to unproven output rows by position.

Each `TableColumn` requires name, scalar type (`integer`, `number`, `string`,
`boolean`), unit, description, and an intentional nullability choice. Integer
columns reject floats and booleans. Nullable cells accept `None` and floating NaN;
infinity, pandas NA/NaT, and nested objects are rejected. Converting missing values
requires an established meaning, not merely a desire to pass validation.

Columns are static, even when a parameter is a list. If output columns depend on
the request, ask the user to choose a compatible result contract. Do not choose
aggregation, a file output, or separate metrics on their behalf.

`FractionOfLocationArea` can derive ratios from numeric source columns with unit
`m2`. Return only source columns. Local normalization needs explicit
`location_areas_m2`; it does not obtain areas from a database.

For `FileOutput(media_type=..., extensions=[...])`, return a `pathlib.Path` to an
existing file beneath `context.temp_dir`. Relative paths resolve beneath that
directory. Escaping paths or symlinks and undeclared suffixes are rejected. Preserve
the original file contents and format, adapting its output destination as needed.
Handlers return the native DataFrame or Path specified by their output declaration.

### Output units

Import `Unit` from `lyra.sdk` and declare, for example, `unit=Unit.SQUARE_METRE`.
Every column must explicitly supply `unit`: a `Unit` member, an exact canonical
string, or `None` when no unit applies. JSON uses canonical strings or `null`.
Labels, identifiers, and booleans normally use `None`; numeric identifiers can
also have no applicable unit. Omission, unknown values, aliases, and incorrect
casing are rejected.

| Enum member | Value | Meaning |
| --- | --- | --- |
| `MILLIMETRE` | `mm` | Millimetres of length. |
| `METRE` | `m` | Metres of length. |
| `KILOMETRE` | `km` | Kilometres of length. |
| `SQUARE_METRE` | `m2` | Square metres of area. |
| `SQUARE_KILOMETRE` | `km2` | Square kilometres of area. |
| `HECTARE` | `ha` | Hectares of area. |
| `DEGREE_CELSIUS` | `degC` | Temperature in degrees Celsius. |
| `KELVIN` | `K` | Temperature in kelvins. |
| `SECOND` | `s` | Duration in seconds. |
| `DAY` | `day` | Duration in days. |
| `YEAR` | `year` | Duration in years; document the workflow's year convention. |
| `CALENDAR_YEAR` | `calendar_year` | Calendar-year value, not an elapsed duration. |
| `COUNT` | `count` | Number of entities; describe what is counted. |
| `RATIO` | `ratio` | Quotient; describe its numerator and denominator. |
| `PERCENT` | `percent` | Value expressed per hundred (50 means 50%). |
| `SCORE` | `score` | Workflow-defined scale; describe its meaning and interpretation. |
| `DIMENSIONLESS` | `dimensionless` | Known unitless quantity without a more specific applicable unit. |

Units declare meaning; they do not convert values or impose numeric ranges or
column-type restrictions. A ratio is not automatically restricted to 0–1.
`None` means not applicable, never unknown. Establish the unit from the workflow
before selecting an identifier. Do not substitute dimensionless, score, or null
for missing scientific information. If the required unit is absent from this
vocabulary, ask the user about adding it to the SDK; do not invent an identifier
or silently convert the calculation. Progress-reporting units are separate
human-readable labels.

## Package and generate

The project needs static `[project].name` and `version`, direct dependencies, and
`[tool.lyra].factory = "your_package.metrics:create_plugin"` in `pyproject.toml`.
Use actual package and module names. Derive `requires-python` from the existing
project policy and dependency requirements, and verify that it includes the
interpreter used for testing. For this SDK baseline the minimum is Python 3.11;
other workflow dependencies or project policy may require a later version. Do
not choose a newer minimum arbitrarily or claim validation on an excluded version.
Declare `lyra-sdk`, and any directly imported
packages such as pandas, pydantic, or `lyra-utils`. GeoPandas belongs in dependencies
when imported by the existing workflow. Use approved dependency sources; these
instructions do not assume Lyra packages have been published to PyPI.

For a new package using Hatch, configure package discovery and include the manifest
as shared data and in source archives. For example, with package `your_package`
and distribution `your-plugin`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["your_package"]

[tool.hatch.build.targets.wheel.shared-data]
"lyra.plugin.json" = "share/lyra/plugins/your-plugin/lyra.plugin.json"

[tool.hatch.build.targets.sdist]
include = ["your_package", "lyra.plugin.json", "pyproject.toml"]
```

The shared-data directory uses the normalized distribution name: lowercase, with
runs of dots, underscores, and hyphens replaced by a hyphen. The manifest's name
and version must agree with package metadata. Preserve an existing build backend
and use its equivalent configuration rather than replacing it.

Run in the plugin environment:

```sh
uv run lyra-plugin build-manifest
uv run lyra-plugin describe
uv run lyra-plugin check-manifest
uv run pytest
```

The generator imports the factory to derive the manifest. Avoid import-time
calculation, data downloads, or service connections. Include the generated root
`lyra.plugin.json` in the deliverable, never edit it by hand. The API reads this
artifact; workers import the factory and reject stale contracts.

## Local verification and requests

Decorated functions remain callable. Direct calls do not prepare parameters,
resolve spatial references, inject context, or normalize results. In tests:

1. Construct resolved `LocationInput` geometry with representative properties,
   distinct values, and IDs in a nonlexical order.
2. Call `plugin.prepare_parameters(metric_name, values)` to apply parameter
   validation and defaults when the metric declares a parameter model. It raises
   `MetricInputError` for invalid values. Skip preparation and omit `parameters`
   for a parameterless handler: calling this helper then raises
   `PluginDefinitionError`, even with an empty dictionary.
3. Call the adapter and original calculation with the same inputs, comparing
   values and row/column identities, not just shapes.
4. Call `plugin.normalize_result(metric_name, frame, job_id="local-test",
   location=location)`. It raises `MetricResultError` for invalid output.
   For files pass `temp_dir`; for area derivations also pass `location_areas_m2`.
5. Exercise invalid parameters and result mismatches; for files test outside-path
   rejection. Supply a strict fake context only if the handler uses it.

These SDK helpers require no API, Redis, RQ, PostGIS, or Earth Engine connection.
They cannot prove that the calculation's data sources or scientific assumptions
are valid. Validate the existing workflow independently using its own tests.
If an adapter explicitly raises `MetricInputError`, its constructor takes
`(metric, path, message)`, for example `MetricInputError("zone_score",
"location", "Required zone attributes are missing")`; it is not a single-message
exception.

Requests nest ordinary values under `input.parameters` and a supported spatial
reference under `input.location` (and/or `input.bounds`). Even all-default parameter
models require a `parameters` object; parameterless metrics omit it. For the
example, explicit GeoJSON must include its required `base_value` and `category`
properties. Do not suggest an administrative-zone reference unless those
attributes can actually be supplied by that workflow.

## Further reading

Use the documentation version corresponding to the target SDK; the following
links are the development source and can change:

- [Plugin authoring](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/authoring.md)
- [Packaging and deployment](https://github.com/RodolfoFigueroa/lyra/blob/main/docs/src/content/docs/plugins/publish-and-debug.md)
- [SDK source](https://github.com/RodolfoFigueroa/lyra/tree/main/packages/lyra_sdk)

For a fixed reference, replace `main` with the selected product release tag or
commit. Installed copies of this skill do not update automatically.
