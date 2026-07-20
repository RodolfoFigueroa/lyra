from functools import partial
from typing import Any

from sqlalchemy.engine import Engine

from lyra_app.converters.bounds import (
    load_from_cvegeos as load_bounds_from_cvegeos,
)
from lyra_app.converters.bounds import (
    load_from_geojson as load_bounds_from_geojson,
)
from lyra_app.converters.bounds import (
    load_from_met_zone_code as load_bounds_from_met_zone_code,
)
from lyra_app.converters.location import (
    load_from_cvegeos as load_location_from_cvegeos,
)
from lyra_app.converters.location import (
    load_from_geojson as load_location_from_geojson,
)
from lyra_app.converters.location import (
    load_from_met_zone_code as load_location_from_met_zone_code,
)


def build_converter_map(engine: Engine) -> dict[str, dict[str, Any]]:
    return {
        "location": {
            "cvegeo_list": partial(load_location_from_cvegeos, engine=engine),
            "met_zone_code": partial(load_location_from_met_zone_code, engine=engine),
            "geojson": load_location_from_geojson,
        },
        "bounds": {
            "cvegeo_list": partial(load_bounds_from_cvegeos, engine=engine),
            "met_zone_code": partial(load_bounds_from_met_zone_code, engine=engine),
            "geojson": load_bounds_from_geojson,
        },
    }


__all__ = ["build_converter_map"]
