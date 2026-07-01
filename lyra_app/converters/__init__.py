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

converter_map = {
    "location": {
        "cvegeo_list": load_location_from_cvegeos,
        "met_zone_code": load_location_from_met_zone_code,
        "geojson": load_location_from_geojson,
    },
    "bounds": {
        "cvegeo_list": load_bounds_from_cvegeos,
        "met_zone_code": load_bounds_from_met_zone_code,
        "geojson": load_bounds_from_geojson,
    },
}

__all__ = ["converter_map"]
