from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

SMOKE_PLUGIN_DIR = Path(__file__).parents[1] / "examples" / "lyra-plugin"
SMOKE_METRIC_QUEUES = {
    "smoke_table_metric": "interactive",
    "smoke_file_metric": "interactive",
    "smoke_cancel_metric": "interactive",
}


def directory_uri(path: Path) -> str:
    return f"dir://{quote(path.resolve().as_posix(), safe='/')}"


def smoke_plugin_uri(path: Path = SMOKE_PLUGIN_DIR) -> str:
    return directory_uri(path)


def feature_collection(feature_ids: tuple[str, ...] = ("area-1",)) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for index, feature_id in enumerate(feature_ids):
        offset = index * 0.02
        features.append(
            {
                "id": feature_id,
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-99.20 + offset, 19.30],
                            [-99.10 + offset, 19.30],
                            [-99.10 + offset, 19.40],
                            [-99.20 + offset, 19.40],
                            [-99.20 + offset, 19.30],
                        ]
                    ],
                },
                "properties": {},
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def geojson_location_payload(
    feature_ids: tuple[str, ...] = ("area-1",),
) -> dict[str, Any]:
    return {
        "data_type": "geojson",
        "value": feature_collection(feature_ids),
    }
