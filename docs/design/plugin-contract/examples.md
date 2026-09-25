# Plugin contract development specimens

Status: **SDK specimens, end-to-end integration, and final verification complete**.
The six specimens run in `tests/fixtures/contract_plugin/` against the feature-branch
SDK. The runnable smoke-test plugin and application use the same format-5 contract.

The [contract](contract.md) is normative. The [handoff](handoff.md) maps these
examples to implemented acceptance tests. Code fences are syntactically complete;
sections identified as separate files belong to the same illustrative plugin project.

## E1: Wrap an existing calculation

This deterministic calculation uses only pandas. It can be called independently
of Lyra. A real adapter can likewise call a library that accepts a GeoDataFrame,
using the existing `convert_geojson_to_gdf` utility at the adapter boundary.

Example `example_metrics/calculation.py`:

```python
import pandas as pd


def calculate_capacity(
    feature_ids: list[str], *, units_per_feature: int = 10
) -> pd.DataFrame:
    return pd.DataFrame(
        {"capacity": [units_per_feature] * len(feature_ids)},
        index=feature_ids,
    )
```

Example `example_metrics/metrics.py` (later examples add definitions to this file):

```python
import pandas as pd
from pydantic import Field

from lyra.sdk import (
    LocationInput,
    MetricParameters,
    TableColumn,
    TableOutput,
    metric,
)

from .calculation import calculate_capacity


class CapacityParameters(MetricParameters):
    units: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Capacity assigned to each selected feature.",
        examples=[5, 10],
    )


CAPACITY_OUTPUT = TableOutput(
    columns=[
        TableColumn(
            name="capacity",
            type="integer",
            unit="units",
            description="Capacity of the selected feature.",
        )
    ]
)


@metric(
    name="capacity",
    description="Assign a capacity to each selected feature.",
    output=CAPACITY_OUTPUT,
)
def capacity(parameters: CapacityParameters, location: LocationInput) -> pd.DataFrame:
    return calculate_capacity(
        [feature.id for feature in location.features],
        units_per_feature=parameters.units,
    )
```

Request R1 (valid; expected rows depend on the selected metropolitan zone):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"units": 5}
  },
  "idempotency_key": "capacity-example-1"
}
```

Request R2 (valid; the worker applies `units=10`):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "cvegeo_list", "value": ["09002"]},
    "parameters": {}
  }
}
```

Request R3 (schema-invalid, unknown parameter):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"untis": 5}
  }
}
```

Request R4 (schema-invalid, missing required parameter object):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"}
  }
}
```

Request R5 (schema-invalid, no string-to-integer coercion):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"units": "5"}
  }
}
```

Request R6 (schema-invalid, constraint violation):

```json
{
  "metric": "capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"units": 0}
  }
}
```

## E2: Reuse a Pydantic model

An existing model that already has compatible configuration can be used directly.
This specimen represents an existing permissive model in another library; the
adapter subclasses it without modifying that library. Nested models need their
own compatible configuration as well.

Additional definitions in `example_metrics/metrics.py`:

```python
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from lyra.sdk import LocationInput, metric


class ExistingSettings(BaseModel):
    units: int = Field(default=4, ge=1, description="Units per selected feature.")


class CompatibleSettings(ExistingSettings):
    model_config = ConfigDict(extra="forbid", validate_default=True)


@metric(
    name="reused_capacity",
    description="Use parameters adapted from an existing library.",
    output=CAPACITY_OUTPUT,
)
def reused_capacity(
    parameters: CompatibleSettings, location: LocationInput
) -> pd.DataFrame:
    return calculate_capacity(
        [feature.id for feature in location.features],
        units_per_feature=parameters.units,
    )
```

Request R7 (valid):

```json
{
  "metric": "reused_capacity",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {}
  }
}
```

The result contains `capacity=4` for each resolved feature. Registering a handler
annotated with `ExistingSettings` instead fails definition validation: its unknown
fields/default validation policy is incompatible. Registering the compatible
subclass must not mutate `ExistingSettings.model_config`.

## E3: Nested parameters, ordinary lists, and null

Additional definitions in `example_metrics/metrics.py`:

```python
import pandas as pd
from pydantic import Field

from lyra.sdk import LocationInput, MetricParameters, TableColumn, TableOutput, metric


class Selection(MetricParameters):
    activity_codes: list[str] = Field(
        min_length=1, description="Activity codes included in the selection."
    )


class SelectionParameters(MetricParameters):
    selection: Selection = Field(description="Activity selection settings.")
    threshold: int | None = Field(
        ge=0, description="Required threshold; null disables the threshold."
    )
    label: str | None = Field(default=None, description="Optional descriptive label.")


@metric(
    name="selection_size",
    description="Count selected activity codes above an optional threshold.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="selected_count",
                type="integer",
                unit="codes",
                description="Number of selected codes passing the threshold.",
            )
        ]
    ),
)
def selection_size(
    parameters: SelectionParameters, location: LocationInput
) -> pd.DataFrame:
    count = len(parameters.selection.activity_codes)
    if parameters.threshold is not None and count < parameters.threshold:
        count = 0
    return pd.DataFrame(
        {"selected_count": [count] * len(location.features)},
        index=[feature.id for feature in location.features],
    )
```

Request R8 (valid; `selected_count=2` for every resolved feature):

```json
{
  "metric": "selection_size",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {
      "selection": {"activity_codes": ["311", "312"]},
      "threshold": null
    }
  }
}
```

The output always has one `selected_count` column. List length/contents do not
create columns or cause Lyra to invoke the handler repeatedly. Omitting
`threshold`, supplying `selection.typo`, or sending an empty code list is
schema-invalid. Omitting `label` is valid and applies its null default.

## E4: Both location and bounds

This deterministic example observes the bounding geometry without needing a live
database. Real computations can convert it to their existing geometry types.

Additional definitions in `example_metrics/metrics.py`:

```python
import pandas as pd

from lyra.sdk import BoundsInput, LocationInput, TableColumn, TableOutput, metric


@metric(
    name="bounded_features",
    description="Describe the supplied bounds for each selected location.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="bounds_type",
                type="string",
                unit="geometry_type",
                description="Geometry type of the supplied bounding feature.",
            )
        ]
    ),
)
def bounded_features(location: LocationInput, bounds: BoundsInput) -> pd.DataFrame:
    return pd.DataFrame(
        {"bounds_type": [bounds.features[0].geometry.type] * len(location.features)},
        index=[feature.id for feature in location.features],
    )
```

Request R9 (valid through REST/Python client):

```json
{
  "metric": "bounded_features",
  "input": {
    "location": {"data_type": "cvegeo_list", "value": ["09002"]},
    "bounds": {"data_type": "met_zone_code", "value": "09.01"}
  }
}
```

Each output row contains the resolved bounds geometry type. Both wrappers are
required and `parameters` is not accepted. MCP can discover this metric but its
single-metropolitan-zone execution helper returns `unsupported_spatial_shape`.

## E5: File output with context and no parameters

Additional definitions in `example_metrics/metrics.py`:

```python
from pathlib import Path

from lyra.sdk import FileOutput, LocationInput, RunContext, metric


@metric(
    name="feature_report",
    description="Write the selected feature identifiers to a text report.",
    output=FileOutput(media_type="text/plain", extensions=[".txt"]),
)
def feature_report(location: LocationInput, context: RunContext) -> Path:
    context.check_cancelled()
    context.logger.info("Writing %d feature identifiers", len(location.features))
    destination = context.temp_dir / "features.txt"
    destination.write_text(
        "".join(f"{feature.id}\n" for feature in location.features), encoding="utf-8"
    )
    return destination
```

Request R10 (valid):

```json
{
  "metric": "feature_report",
  "input": {
    "location": {"data_type": "cvegeo_list", "value": ["09002"]}
  }
}
```

For the local two-feature fixture below, the file contains `area_a\narea_b\n`.
The worker supplies job metadata and `text/plain`. Nonempty MCP parameters fail;
the MCP helper's default empty parameters cause it to omit the request property.
An absolute path outside `context.temp_dir`, an escaping symlink, a nonexistent
file, or a `.csv` suffix fails result validation.

## E6: Semantic validation in the worker

Additional definitions in `example_metrics/metrics.py`:

```python
from typing import Self

import pandas as pd
from pydantic import Field, model_validator

from lyra.sdk import LocationInput, MetricParameters, TableColumn, TableOutput, metric


class IntervalParameters(MetricParameters):
    lower: int = Field(ge=0, description="Lower interval boundary.")
    upper: int = Field(ge=0, description="Upper interval boundary.")

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("lower must not exceed upper")
        return self


@metric(
    name="interval_width",
    description="Return the width of a valid interval for each location.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="width",
                type="integer",
                unit="units",
                description="Difference between the upper and lower boundary.",
            )
        ]
    ),
)
def interval_width(
    parameters: IntervalParameters, location: LocationInput
) -> pd.DataFrame:
    return pd.DataFrame(
        {"width": [parameters.upper - parameters.lower] * len(location.features)},
        index=[feature.id for feature in location.features],
    )
```

Request R11 (schema-valid but semantically invalid):

```json
{
  "metric": "interval_width",
  "input": {
    "location": {"data_type": "met_zone_code", "value": "09.01"},
    "parameters": {"lower": 20, "upper": 10}
  }
}
```

Submission succeeds if dependencies/capacity permit. Parameter preparation fails
in the worker with `invalid_input` and the ordering message; the handler is not
called. Using `lower=10, upper=20` succeeds and yields `width=10`. Directly
constructing the invalid model in a local test raises Pydantic `ValidationError`;
the shared preparation operation wraps that as `MetricInputError`.

## Project registration and workflow

Example `example_metrics/plugin.py`:

```python
from lyra.sdk import PluginDefinition

from .metrics import (
    bounded_features,
    capacity,
    feature_report,
    interval_width,
    reused_capacity,
    selection_size,
)


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[
            capacity,
            reused_capacity,
            selection_size,
            bounded_features,
            feature_report,
            interval_width,
        ]
    )


def create_capacity_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[capacity])
```

The package also contains an empty `example_metrics/__init__.py`. Proposed
`pyproject.toml` (an ordinary package; no new dependency is added to this repository):

```toml
[project]
name = "lyra-contract-examples"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["lyra-sdk", "pydantic", "pandas"]

[project.optional-dependencies]
test = ["pytest"]

[tool.lyra]
factory = "example_metrics.plugin:create_plugin"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["example_metrics"]
```

The development workflow is:

```sh
uv sync --extra test
uv run pytest
uv run lyra-plugin describe capacity
uv run lyra-plugin describe capacity --json
uv run lyra-plugin build-manifest
uv run lyra-plugin check-manifest
```

The describe output must show `units` as an omittable integer with default 10,
bounds 1–100, its description/examples, the required location wrapper, and the
fixed capacity column. Authors commit the generated manifest and never edit its
schema manually. `check-manifest` fails after an input/output declaration changes
until regeneration. Distribution builds are outside the verification scope.

## Direct local calls and shared normalization

Local test specimen:

```python
from lyra.sdk.models.geometry import GeoJSON

from example_metrics.metrics import CapacityParameters, capacity
from example_metrics.plugin import create_plugin


def local_location() -> GeoJSON:
    return GeoJSON.model_validate(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": feature_id,
                    "geometry": {"type": "Point", "coordinates": coordinates},
                    "properties": {},
                }
                for feature_id, coordinates in [
                    ("area_a", [-99.1, 19.4]),
                    ("area_b", [-99.2, 19.5]),
                ]
            ],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }
    )


def test_capacity_direct_and_validated() -> None:
    location = local_location()
    frame = capacity(parameters=CapacityParameters(units=5), location=location)
    assert frame.index.tolist() == ["area_a", "area_b"]
    assert frame["capacity"].tolist() == [5, 5]

    plugin = create_plugin()
    prepared = plugin.prepare_parameters("capacity", {"units": 5})
    assert isinstance(prepared, CapacityParameters)
    result = plugin.normalize_result(
        "capacity", frame, job_id="local-test", location=location
    )
    assert result.model_dump(mode="json") == {
        "kind": "table",
        "job_id": "local-test",
        "status": "succeeded",
        "index": ["area_a", "area_b"],
        "columns": ["capacity"],
        "data": [[5], [5]],
    }
```

The executable equivalents live in the SDK and integration test suites. File tests can pass a small fake context with `temp_dir`,
`logger`, and `check_cancelled`, then normalize the returned path with that same
temporary directory. No live database is needed for either example.

## Complete minimal manifest specimen

The following is a format-5 manifest for **only E1**, using the explicit
`create_capacity_plugin` factory above. The full example project's generated
manifest would contain all six metrics. The schema below is generated in one
root-model pass using the currently installed Pydantic and spatial wrapper
models; it is an illustrative contract fixture, not a committed plugin artifact.

```json
{
  "factory": "example_metrics.plugin:create_capacity_plugin",
  "metrics": [
    {
      "description": "Assign a capacity to each selected feature.",
      "name": "capacity",
      "output": {
        "columns": [
          {
            "description": "Capacity of the selected feature.",
            "name": "capacity",
            "nullable": false,
            "type": "integer",
            "unit": "units"
          }
        ],
        "kind": "table"
      },
      "request_schema": {
        "$defs": {
          "CRS": {
            "additionalProperties": false,
            "description": "GeoJSON coordinate reference system object.",
            "properties": {
              "properties": {
                "$ref": "#/$defs/CRSProperties",
                "description": "CRS properties."
              },
              "type": {
                "const": "name",
                "description": "CRS object type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "type",
              "properties"
            ],
            "title": "CRS",
            "type": "object"
          },
          "CRSProperties": {
            "additionalProperties": false,
            "description": "Coordinate reference system properties.",
            "properties": {
              "name": {
                "description": "CRS identifier such as EPSG:4326.",
                "minLength": 1,
                "title": "Name",
                "type": "string"
              }
            },
            "required": [
              "name"
            ],
            "title": "CRSProperties",
            "type": "object"
          },
          "CVEGEOList": {
            "additionalProperties": false,
            "description": "A list of same-level INEGI CVEGEO identifiers.",
            "properties": {
              "data_type": {
                "const": "cvegeo_list",
                "default": "cvegeo_list",
                "title": "Data Type",
                "type": "string"
              },
              "value": {
                "items": {
                  "type": "string"
                },
                "title": "Value",
                "type": "array"
              }
            },
            "required": [
              "value"
            ],
            "title": "CVEGEOList",
            "type": "object"
          },
          "CapacityParameters": {
            "additionalProperties": false,
            "properties": {
              "units": {
                "default": 10,
                "description": "Capacity assigned to each selected feature.",
                "examples": [
                  5,
                  10
                ],
                "maximum": 100,
                "minimum": 1,
                "title": "Units",
                "type": "integer"
              }
            },
            "title": "CapacityParameters",
            "type": "object"
          },
          "Feature": {
            "additionalProperties": false,
            "description": "GeoJSON feature that may contain point, polygon, or multi-polygon geometry.",
            "properties": {
              "geometry": {
                "anyOf": [
                  {
                    "$ref": "#/$defs/PointGeometry"
                  },
                  {
                    "$ref": "#/$defs/PolygonGeometry"
                  },
                  {
                    "$ref": "#/$defs/MultiPolygonGeometry"
                  }
                ],
                "description": "Feature geometry.",
                "title": "Geometry"
              },
              "id": {
                "description": "Stable feature identifier.",
                "minLength": 1,
                "title": "Id",
                "type": "string"
              },
              "properties": {
                "additionalProperties": true,
                "description": "Feature properties.",
                "title": "Properties",
                "type": "object"
              },
              "type": {
                "const": "Feature",
                "description": "GeoJSON feature type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "id",
              "type",
              "geometry",
              "properties"
            ],
            "title": "Feature",
            "type": "object"
          },
          "GeoJSON": {
            "additionalProperties": false,
            "description": "GeoJSON FeatureCollection accepted by explicit location inputs.",
            "properties": {
              "crs": {
                "$ref": "#/$defs/CRS",
                "description": "Coordinate reference system for all features."
              },
              "features": {
                "description": "One or more GeoJSON features.",
                "items": {
                  "$ref": "#/$defs/Feature"
                },
                "minItems": 1,
                "title": "Features",
                "type": "array"
              },
              "type": {
                "const": "FeatureCollection",
                "description": "GeoJSON collection type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "type",
              "features",
              "crs"
            ],
            "title": "GeoJSON",
            "type": "object"
          },
          "GeoJSONLocation": {
            "additionalProperties": false,
            "description": "A GeoJSON feature collection accepted as a location.",
            "properties": {
              "data_type": {
                "const": "geojson",
                "default": "geojson",
                "title": "Data Type",
                "type": "string"
              },
              "value": {
                "$ref": "#/$defs/GeoJSON"
              }
            },
            "required": [
              "value"
            ],
            "title": "GeoJSONLocation",
            "type": "object"
          },
          "MetZoneCode": {
            "additionalProperties": false,
            "description": "A metropolitan-zone code reference.",
            "properties": {
              "data_type": {
                "const": "met_zone_code",
                "default": "met_zone_code",
                "title": "Data Type",
                "type": "string"
              },
              "value": {
                "minLength": 1,
                "title": "Value",
                "type": "string"
              }
            },
            "required": [
              "value"
            ],
            "title": "MetZoneCode",
            "type": "object"
          },
          "MultiPolygonGeometry": {
            "additionalProperties": false,
            "description": "GeoJSON multi-polygon geometry.",
            "properties": {
              "coordinates": {
                "description": "Multi-polygon rings and coordinate pairs.",
                "items": {
                  "items": {
                    "items": {
                      "items": {
                        "type": "number"
                      },
                      "type": "array"
                    },
                    "type": "array"
                  },
                  "type": "array"
                },
                "title": "Coordinates",
                "type": "array"
              },
              "type": {
                "const": "MultiPolygon",
                "description": "GeoJSON geometry type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "type",
              "coordinates"
            ],
            "title": "MultiPolygonGeometry",
            "type": "object"
          },
          "PointGeometry": {
            "additionalProperties": false,
            "description": "GeoJSON point geometry.",
            "properties": {
              "coordinates": {
                "description": "Point coordinate pair.",
                "items": {
                  "type": "number"
                },
                "title": "Coordinates",
                "type": "array"
              },
              "type": {
                "const": "Point",
                "description": "GeoJSON geometry type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "type",
              "coordinates"
            ],
            "title": "PointGeometry",
            "type": "object"
          },
          "PolygonGeometry": {
            "additionalProperties": false,
            "description": "GeoJSON polygon geometry.",
            "properties": {
              "coordinates": {
                "description": "Polygon rings and coordinate pairs.",
                "items": {
                  "items": {
                    "items": {
                      "type": "number"
                    },
                    "type": "array"
                  },
                  "type": "array"
                },
                "title": "Coordinates",
                "type": "array"
              },
              "type": {
                "const": "Polygon",
                "description": "GeoJSON geometry type.",
                "title": "Type",
                "type": "string"
              }
            },
            "required": [
              "type",
              "coordinates"
            ],
            "title": "PolygonGeometry",
            "type": "object"
          }
        },
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": false,
        "properties": {
          "location": {
            "discriminator": {
              "mapping": {
                "cvegeo_list": "#/$defs/CVEGEOList",
                "geojson": "#/$defs/GeoJSONLocation",
                "met_zone_code": "#/$defs/MetZoneCode"
              },
              "propertyName": "data_type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/CVEGEOList"
              },
              {
                "$ref": "#/$defs/GeoJSONLocation"
              },
              {
                "$ref": "#/$defs/MetZoneCode"
              }
            ],
            "title": "Location"
          },
          "parameters": {
            "$ref": "#/$defs/CapacityParameters"
          }
        },
        "required": [
          "location",
          "parameters"
        ],
        "title": "CapacityRequest",
        "type": "object"
      },
      "spatial_inputs": {
        "location": "location"
      }
    }
  ],
  "plugin": {
    "name": "lyra-contract-examples",
    "version": "0.1.0"
  },
  "schema_version": 5
}
```

R1 and R2 inputs satisfy this schema. R3–R6 inputs do not. The manifest excludes
deployment routing and execution metadata. The public catalog exposes the same
metric schema and output contract while keeping factory details internal.

## Negative acceptance checklist

| Case | Required result |
| --- | --- |
| Scalar `units` is `"5"`, null, zero, or 101 | API/schema rejection before dispatch. |
| Nullable `threshold` is omitted | API rejection; nullable is not omittable. |
| Unknown root or nested model field | API rejection, with the field path. |
| Root/nested model has permissive extra policy | Definition error naming the model path. |
| Invalid default or declared example | Manifest generation error against the field schema. |
| Default factory, recursive model, alias, unsupported type | Definition error; no partial or fallback manifest. |
| Handler has positional-only arguments, variadics, or is async | Definition error explaining the supported signature. |
| Python cross-field validator rejects schema-valid input | Failed job with `invalid_input`; handler not called. |
| Changed live definition disagrees with manifest | Worker startup failure and CLI stale-manifest failure. |
| DataFrame has duplicate/reordered/missing/non-string indices | `MetricResultError`; no automatic alignment/stringification. |
| Columns differ in name/order or contain extras | `MetricResultError` identifying the mismatch. |
| Integer column contains boolean or float cells | Invalid result, without coercion. |
| Frame has separate integer and float columns | Preserve per-column scalar types; do not promote integers during extraction. |
| Nullable number contains None/NaN | JSON null; same values in a nonnullable column fail. |
| Cell contains infinity, pandas NA/NaT, or a nested object | Invalid result with row/column context. |
| File is outside the job directory or has wrong suffix | Invalid result; escaping symlinks are rejected. |
| Native result is a Series/dictionary/terminal model | Invalid result; adapter returns DataFrame or Path. |
| Area-fraction derivation lacks valid supplied areas | Invalid result; normalizer does not query a database. |
| MCP attempts the dual-spatial specimen | `unsupported_spatial_shape`; REST remains supported. |

For later idempotency tests, R2's omitted default and an explicit `units=10` are
different submitted requests. Repeating either exact request with the same key
reuses its job; reusing the key for the other request is a conflict.
