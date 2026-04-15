from pyproj import CRS, Transformer
from typing import Sequence, Literal
import geopandas as gpd
import osmnx as ox
from lyra.db import engine


def load_geometries_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    columns: Sequence[str],
    table_name: str,
) -> gpd.GeoDataFrame:
    with engine.connect() as conn:
        return gpd.read_postgis(
            f"""
            SELECT {", ".join(columns)} FROM {table_name}
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 6372))
            """,
            conn,
            params={
                "xmin": xmin,
                "ymin": ymin,
                "xmax": xmax,
                "ymax": ymax,
            },
            geom_col="geometry",
        )


def load_roads_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float, *, coords_crs: str
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    crs = CRS.from_user_input(coords_crs)
    latlon_crs = CRS.from_epsg(4326)
    if crs != latlon_crs:
        transformer = Transformer.from_crs(crs, latlon_crs, always_xy=True)
        xmin, ymin = transformer.transform(xmin, ymin)
        xmax, ymax = transformer.transform(xmax, ymax)

    bounds = (xmin, ymin, xmax, ymax)
    g = ox.graph_from_bbox(bbox=bounds, network_type="drive")
    nodes, edges = ox.graph_to_gdfs(g)

    nodes = nodes.to_crs(crs).filter(["geometry"])
    edges = edges.to_crs(crs).reset_index()[["u", "v", "length"]]

    return nodes, edges


def load_denue_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["per_ocu", "codigo_act", "geometry"],
        table_name="denue_05_2025",
    )


def load_mesh_from_bounds(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    *,
    level: Literal[4, 5, 6, 7, 8, 9] = 9,
) -> gpd.GeoDataFrame:
    return load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["codigo", "geometry"],
        table_name=f"mesh_level_{level}",
    )


def merge_mesh_and_census(
    agebs: gpd.GeoDataFrame, mesh: gpd.GeoDataFrame, wanted_cols: Sequence[str]
) -> gpd.GeoDataFrame:
    wanted_cols = list(wanted_cols)

    crs = agebs.crs
    if crs is None:
        err = "AGEBs GeoDataFrame must have a defined CRS to merge with mesh."
        raise ValueError(err)

    mesh = mesh.to_crs(crs)
    mesh_agg = (
        mesh.overlay(
            agebs[wanted_cols + ["geometry"]].assign(ageb_area=lambda df: df.area)
        )
        .assign(
            area_fraction=lambda df: df.area / df.ageb_area,
        )
        .drop(columns=["ageb_area", "geometry"])
    )
    for c in mesh_agg.columns:
        if c in {"area_fraction", "CODIGO"}:
            continue
        mesh_agg[c] = mesh_agg[c] * mesh_agg["area_fraction"]
    mesh_agg = mesh_agg.drop(columns="area_fraction").groupby("CODIGO").sum()
    mesh = mesh.merge(mesh_agg, on="CODIGO", how="left").fillna(0.0)
    return mesh
