from typing import Literal
import geopandas as gpd
import pandas as pd
import pandana as pdna
from lyra.functions.utils import convert_geojson_to_gdf
from lyra.models.wrappers import ExplicitLocationAPI
from lyra.constants import PER_OCU_TO_NUM_WORKERS_MAP
from lyra.functions.load.osm import load_roads_from_bounds
from lyra.functions.load.db import (
    load_denue_from_bounds,
    load_mesh_from_bounds,
)


def compute_accessibility_jobs(
    df: gpd.GeoDataFrame,
    denue: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    *,
    group_patterns: list[str] | None = None,
) -> pd.DataFrame:
    mesh = mesh[["geometry"]]

    net_accessibility = pdna.Network(
        nodes["geometry"].x.copy(),
        nodes["geometry"].y.copy(),
        edges["u"].copy(),
        edges["v"].copy(),
        edges[["length"]].copy(),
    )

    crs = df.crs
    if crs is None:
        err = "AGEBs GeoDataFrame must have a defined CRS."
        raise ValueError(err)

    denue = (
        denue.assign(num_workers=lambda x: x["per_ocu"].map(PER_OCU_TO_NUM_WORKERS_MAP))
        .drop(columns=["per_ocu"])
        .to_crs(crs)
        .assign(
            osmid=lambda df: net_accessibility.get_node_ids(
                df["geometry"].x, df["geometry"].y, mapping_distance=1000
            )
        )
    )

    denue_osmid = denue.groupby("osmid")["num_workers"].sum()
    net_accessibility.set(denue_osmid.index, variable=denue_osmid.values, name="jobs")

    if group_patterns is not None:
        for i, pat in enumerate(group_patterns):
            denue_osmid_group = (
                denue.loc[lambda df: df["codigo_act"].str.match(pat)]
                .groupby("osmid")["num_workers"]
                .sum()
            )
            net_accessibility.set(
                denue_osmid_group.index,
                variable=denue_osmid_group.values,
                name=f"jobs_{i}",
            )

    mesh = mesh.assign(
        osmid=lambda df: net_accessibility.get_node_ids(
            df["geometry"].centroid.x,
            df["geometry"].centroid.y,
            mapping_distance=1000,
        )
    ).merge(
        net_accessibility.aggregate(20000, "sum", "exp", name="jobs")
        .rename("jobs")
        .fillna(0),
        on="osmid",
        how="left",
    )

    if group_patterns is not None:
        for i in range(len(group_patterns)):
            mesh = mesh.merge(
                net_accessibility.aggregate(20000, "sum", "exp", name=f"jobs_{i}")
                .rename(f"jobs_{i}")
                .fillna(0),
                on="osmid",
                how="left",
            )

    return pd.DataFrame(
        df[["cvegeo", "geometry"]]
        .sjoin(mesh, how="left")
        .drop(columns=["osmid", "index_right", "geometry"])
        .groupby("cvegeo")
        .mean(),
    )


METRIC_DESCRIPTION: str = "Computes job accessibility scores for each spatial unit using road network analysis and employment data."


def calculate(
    data: ExplicitLocationAPI,
    group_patterns: list[str] | None = None,
    year: Literal[2020, 2021, 2022, 2023, 2024, 2025] | None = None,
) -> dict:
    if year is None:
        year = 2025

    df = convert_geojson_to_gdf(data)
    df = df.to_crs("EPSG:6372")
    xmin, ymin, xmax, ymax = df["geometry"].buffer(10_000).total_bounds

    df_denue = load_denue_from_bounds(xmin, ymin, xmax, ymax, year=year)
    df_mesh = load_mesh_from_bounds(xmin, ymin, xmax, ymax)

    nodes, edges = load_roads_from_bounds(
        xmin, ymin, xmax, ymax, bounds_crs="EPSG:6372"
    )
    return compute_accessibility_jobs(
        df, df_denue, df_mesh, nodes, edges, group_patterns=group_patterns
    ).to_dict(orient="index")
