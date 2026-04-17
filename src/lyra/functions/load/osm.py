import geopandas as gpd
import osmnx as ox
from pyproj import CRS, Transformer


def _project_bounds_to_latlon(
    xmin: float, ymin: float, xmax: float, ymax: float, bounds_crs: str | CRS
) -> tuple[float, float, float, float]:
    crs = CRS.from_user_input(bounds_crs)
    latlon_crs = CRS.from_epsg(4326)

    if crs != latlon_crs:
        transformer = Transformer.from_crs(crs, latlon_crs, always_xy=True)
        xmin, ymin = transformer.transform(xmin, ymin)
        xmax, ymax = transformer.transform(xmax, ymax)

    return xmin, ymin, xmax, ymax


def load_roads_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float, *, bounds_crs: str | CRS
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    xmin, ymin, xmax, ymax = _project_bounds_to_latlon(
        xmin, ymin, xmax, ymax, bounds_crs
    )

    g = ox.graph_from_bbox(bbox=(xmin, ymin, xmax, ymax), network_type="drive")
    nodes, edges = ox.graph_to_gdfs(g)

    nodes = nodes.to_crs(bounds_crs).filter(["geometry"])
    edges = edges.reset_index()[["u", "v", "length"]]

    return nodes, edges


def load_osm_features_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    bounds_crs: str | CRS,
    tags: dict[str, bool | str | list[str]],
) -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = _project_bounds_to_latlon(
        xmin, ymin, xmax, ymax, bounds_crs
    )
    return ox.features_from_bbox((xmin, ymin, xmax, ymax), tags=tags).to_crs(bounds_crs)
