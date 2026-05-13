"""Utilities for loading road networks and OSM features via OSMnx."""

from typing import Literal

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandana as pdna
from lyra.utils.constants import WALK_SPEED_KPH
from pyproj import CRS, Transformer


def _project_bounds_to_latlon(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    bounds_crs: str | CRS,
) -> tuple[float, float, float, float]:
    """Reproject a bounding box to WGS 84 (EPSG:4326) longitude/latitude.

    If the provided CRS is already WGS 84, the bounds are returned unchanged.

    Args:
        xmin: Minimum x coordinate of the bounding box.
        ymin: Minimum y coordinate of the bounding box.
        xmax: Maximum x coordinate of the bounding box.
        ymax: Maximum y coordinate of the bounding box.
        bounds_crs: CRS of the input coordinates, as an EPSG string or
            ``pyproj.CRS`` object.

    Returns:
        A tuple ``(xmin, ymin, xmax, ymax)`` reprojected to WGS 84
        (longitude/latitude).
    """
    crs = CRS.from_user_input(bounds_crs)
    latlon_crs = CRS.from_epsg(4326)

    if crs != latlon_crs:
        transformer = Transformer.from_crs(crs, latlon_crs, always_xy=True)
        xmin, ymin = transformer.transform(xmin, ymin)
        xmax, ymax = transformer.transform(xmax, ymax)

    return xmin, ymin, xmax, ymax


def load_roads_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    bounds_crs: str | CRS,
    network_type: Literal["drive", "walk"],
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load a road network from OSM for a given bounding box.

    Fetches the road graph via OSMnx, computes edge speeds and travel times,
    and returns nodes and edges as separate GeoDataFrames. Node geometries are
    reprojected to ``bounds_crs``; walk networks use a fixed speed of
    ``WALK_SPEED_KPH``.

    Args:
        xmin: Minimum x coordinate of the bounding box.
        ymin: Minimum y coordinate of the bounding box.
        xmax: Maximum x coordinate of the bounding box.
        ymax: Maximum y coordinate of the bounding box.
        bounds_crs: CRS of the input coordinates.
        network_type: Either ``"drive"`` or ``"walk"``.

    Returns:
        A tuple ``(nodes, edges)`` where:
        - ``nodes`` is a GeoDataFrame of node geometries in ``bounds_crs``.
        - ``edges`` is a DataFrame with columns
          ``["u", "v", "length", "travel_time"]``.
    """
    xmin, ymin, xmax, ymax = _project_bounds_to_latlon(
        xmin,
        ymin,
        xmax,
        ymax,
        bounds_crs,
    )

    g = ox.graph_from_bbox(bbox=(xmin, ymin, xmax, ymax), network_type=network_type)

    if network_type == "drive":
        g = ox.add_edge_speeds(g)
    else:
        nx.set_edge_attributes(g, name="speed_kph", values=WALK_SPEED_KPH)
    g = ox.add_edge_travel_times(g)
    nodes, edges = ox.graph_to_gdfs(g)

    nodes = nodes.to_crs(bounds_crs).filter(["geometry"])
    edges = edges.reset_index()[["u", "v", "length", "travel_time"]]

    return nodes, edges


def load_accessibility_net_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    bounds_crs: str | CRS,
    network_type: Literal["drive", "walk"],
) -> pdna.Network:
    """Build a Pandana accessibility network from OSM roads within a bounding box.

    Loads the road graph via :func:`load_roads_from_bounds` and constructs a
    ``pandana.Network`` ready for accessibility analysis.

    Args:
        xmin: Minimum x coordinate of the bounding box.
        ymin: Minimum y coordinate of the bounding box.
        xmax: Maximum x coordinate of the bounding box.
        ymax: Maximum y coordinate of the bounding box.
        bounds_crs: CRS of the input coordinates.
        network_type: Either ``"drive"`` or ``"walk"``.

    Returns:
        A ``pandana.Network`` built from the OSM road graph, with ``length``
        and ``travel_time`` as edge impedances.
    """
    nodes, edges = load_roads_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        bounds_crs=bounds_crs,
        network_type=network_type,
    )
    return pdna.Network(
        nodes["geometry"].x.copy(),
        nodes["geometry"].y.copy(),
        edges["u"].copy(),
        edges["v"].copy(),
        edges[["length", "travel_time"]].copy(),
    )


def load_osm_features_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    bounds_crs: str | CRS,
    tags: dict[str, bool | str | list[str]],
) -> gpd.GeoDataFrame:
    """Load OSM features within a bounding box filtered by tags.

    Reprojects the bounding box to WGS 84, fetches features from OSM via
    OSMnx, and returns them reprojected to ``bounds_crs``.

    Args:
        xmin: Minimum x coordinate of the bounding box.
        ymin: Minimum y coordinate of the bounding box.
        xmax: Maximum x coordinate of the bounding box.
        ymax: Maximum y coordinate of the bounding box.
        bounds_crs: CRS of the input coordinates (output will match this CRS).
        tags: OSM tag filters as accepted by ``osmnx.features_from_bbox``
            (e.g. ``{"amenity": "school"}``).

    Returns:
        A GeoDataFrame of matching OSM features in ``bounds_crs``.
    """
    xmin, ymin, xmax, ymax = _project_bounds_to_latlon(
        xmin,
        ymin,
        xmax,
        ymax,
        bounds_crs,
    )
    return ox.features_from_bbox((xmin, ymin, xmax, ymax), tags=tags).to_crs(bounds_crs)
