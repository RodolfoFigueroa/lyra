---
title: lyra-utils
description: Shared utility helpers for Lyra plugin code, including GeoJSON conversion, date ranges, and Earth Engine reducers.
---

`lyra-utils` contains optional helpers used by Lyra metric implementations.
Plugin packages can depend on `lyra-sdk` alone for runtime contracts, or add
`lyra-utils` when they need spatial conversion, date helpers, or Earth Engine
reduction helpers.

## Install In A Plugin

Add `lyra-utils` to the plugin package dependencies when you use these helpers:

```toml
[project]
dependencies = [
  "lyra-sdk",
  "lyra-utils",
]
```

## GeoJSON Helpers

Import `convert_geojson_to_gdf` from `lyra.utils.geometry`.

```python
from lyra.sdk.models.geometry import GeoJSON
from lyra.utils.geometry import convert_geojson_to_gdf


def feature_count(input_payload: dict) -> int:
    geojson = GeoJSON.model_validate(input_payload["geometries"])
    gdf = convert_geojson_to_gdf(geojson)
    return len(gdf)
```

`convert_geojson_to_gdf(geojson)` accepts SDK `GeoJSON` or `SingleGeoJSON` and
returns a `geopandas.GeoDataFrame`.

Runner plugins receive `job.input` as a plain dictionary. Parse spatial fields
with `GeoJSON.model_validate()` or `SingleGeoJSON.model_validate()` before
calling this helper. See [Spatial Plugin Inputs](../spatial-plugin-inputs/) for
complete manifest schemas and runner examples.

The returned GeoDataFrame:

| Behavior | Detail |
| --- | --- |
| CRS | Uses `geojson.crs.properties.name`. |
| Rows | One row per GeoJSON feature. |
| Index | Feature IDs become the GeoDataFrame index. |
| Columns | Feature properties plus `geometry`. |

## Date Helpers

Import date helpers from `lyra.utils.date`.

```python
from lyra.utils.date import get_date_range, get_season_date_range

month_start, month_end = get_date_range(5, 2025)
season_start, season_end = get_season_date_range("summer", 2025)
```

| Function | Returns |
| --- | --- |
| `get_date_range(month, year)` | First and last day of the month as `YYYY-MM-DD` strings. |
| `get_season_date_range(season, year)` | First and last day of a meteorological season as `YYYY-MM-DD` strings. |

Valid seasons are `winter`, `spring`, `summer`, and `autumn`. Winter starts in
December of the previous year and ends in February of the provided year.

## Earth Engine Helpers

Import Earth Engine helpers from `lyra.utils.ee`.

```python
from lyra.utils.ee import (
    chunk_gdf,
    compute_gdf,
    convert_gdf_to_ee,
    convert_polygon_to_ee,
    get_reducer_name,
    reduce_ee_image_over_gdf_factory,
)
```

Available helpers:

| Helper | Purpose |
| --- | --- |
| `convert_polygon_to_ee(polygon)` | Convert a Shapely polygon to `ee.Geometry.Polygon`. |
| `convert_gdf_to_ee(gdf)` | Convert a GeoDataFrame to `ee.FeatureCollection`. The GeoDataFrame must be EPSG:4326. |
| `get_reducer_name(reducer)` | Extract names such as `mean` or `sum` from an Earth Engine reducer. |
| `compute_gdf(img, gdf, reducer, scale)` | Run `img.reduceRegions()` over a GeoDataFrame and return a Pandas Series indexed like the input. |
| `chunk_gdf(gdf, chunk_size=1000)` | Yield GeoDataFrame chunks for large requests. |
| `reduce_ee_image_over_gdf_factory(load_img_func, reducer, scale)` | Build a function that reduces an Earth Engine image over an `ExplicitLocationAPI` input. |

`convert_gdf_to_ee()` raises `ValueError` if the GeoDataFrame is missing a CRS
or is not in EPSG:4326.

## Explicit Location Reducers

`reduce_ee_image_over_gdf_factory()` is useful for metrics that reduce an
Earth Engine image over every client-selected location. It creates a callable
that accepts `ExplicitLocationAPI` and returns a dictionary keyed by feature ID.

```python
import ee
from lyra.sdk.types import ExplicitLocationAPI
from lyra.utils.ee import reduce_ee_image_over_gdf_factory


def load_image(bounds: ee.Geometry) -> ee.Image:
    return ee.Image("LANDSAT/LC08/C02/T1_L2/example").clip(bounds)


reduce_locations = reduce_ee_image_over_gdf_factory(
    load_image,
    reducer=ee.Reducer.mean(),
    scale=30,
)


def summarize_locations(locations: ExplicitLocationAPI) -> dict[str, float]:
    return reduce_locations(locations)
```

The factory:

| Step | Behavior |
| --- | --- |
| Input conversion | Converts the explicit location GeoJSON to a GeoDataFrame. |
| CRS handling | Projects locations to EPSG:4326 before sending them to Earth Engine. |
| Image loading | Calls `load_img_func` with an `ee.Geometry.BBox` around all input features. |
| Reduction | Runs the requested reducer at the provided scale for each geometry. |
| Large payloads | Splits oversized Earth Engine requests into chunks and concatenates results. |
