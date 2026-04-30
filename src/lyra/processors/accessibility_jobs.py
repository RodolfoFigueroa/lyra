from typing import Literal
import geopandas as gpd
import pandas as pd
import pandana as pdna
from lyra.functions.utils import convert_geojson_to_gdf
from lyra.models.processors.accessibility_jobs import JobGroupModel
from lyra.models.wrappers import ExplicitLocationAPI
from lyra.constants import PER_OCU_TO_NUM_WORKERS_MAP
from lyra.functions.load.osm import (
    load_accessibility_net_from_bounds,
)
from lyra.functions.utils import get_geometries_osmid
from lyra.functions.load.db import (
    load_denue_from_bounds,
    load_mesh_from_bounds,
)


METRIC_DESCRIPTION: str = "Computes job accessibility scores for each spatial unit using road network analysis and employment data."
ITEMS_DEFAULT = {
    "default": 
        JobGroupModel(
            edge_weights="length",
            max_weight=1000,
            network_type="drive"
        )
}

def calculate_prepare(
    data: ExplicitLocationAPI,
    year: Literal[2020, 2021, 2022, 2023, 2024, 2025] | None = None,
) -> dict:
    wanted_crs = "EPSG:6372"

    if year is None:
        year = 2025

    df = convert_geojson_to_gdf(data)
    df = df.to_crs(wanted_crs)
    xmin, ymin, xmax, ymax = df["geometry"].buffer(10_000).total_bounds

    net_accessibility_drive = load_accessibility_net_from_bounds(
        xmin, ymin, xmax, ymax, bounds_crs="EPSG:6372", network_type="drive"
    )
    net_accessibility_walk = load_accessibility_net_from_bounds(
        xmin, ymin, xmax, ymax, bounds_crs="EPSG:6372", network_type="walk"
    )

    df_denue = (
        load_denue_from_bounds(xmin, ymin, xmax, ymax, year=year)
        .to_crs(wanted_crs)
        .assign(num_workers=lambda x: x["per_ocu"].map(PER_OCU_TO_NUM_WORKERS_MAP))
        .drop(columns=["per_ocu"])
        .assign(
            osmid_drive=lambda df: get_geometries_osmid(df, net_accessibility_drive),
            osmid_walk=lambda df: get_geometries_osmid(df, net_accessibility_walk),
        )
    )

    df_mesh = load_mesh_from_bounds(xmin, ymin, xmax, ymax)[["geometry"]].assign(
        osmid_drive=lambda df: get_geometries_osmid(
            df,
            net_accessibility_drive,
        ),
        osmid_walk=lambda df: get_geometries_osmid(
            df,
            net_accessibility_walk,
        ),
    )
    return {
        "df": df,
        "denue": df_denue,
        "mesh": df_mesh,
        "net_accessibility_drive": net_accessibility_drive,
        "net_accessibility_walk": net_accessibility_walk,
    }


def calculate_for_items(
    item_key: str,
    item: JobGroupModel,
    *,
    df: gpd.GeoDataFrame,
    denue: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    net_accessibility_drive: pdna.Network,
    net_accessibility_walk: pdna.Network,
) -> pd.DataFrame:
    if item.network_type == "drive":
        net_accessibility = net_accessibility_drive
        osmid_col = "osmid_drive"
    elif item.network_type == "walk":
        net_accessibility = net_accessibility_walk
        osmid_col = "osmid_walk"

    denue_osmid_group = (
        denue.loc[lambda df: df["codigo_act"].str.match(item.pattern)]
        .groupby(osmid_col)["num_workers"]
        .sum()
    )

    # TODO: This mutates the network, which is not ideal. We should rewrite this.
    net_accessibility.set(
        denue_osmid_group.index,
        variable=denue_osmid_group.values,
        name=f"jobs_{item_key}",
    )

    mesh = mesh.merge(
        net_accessibility.aggregate(
            item.max_weight,
            type="sum",
            decay="exp",
            name=f"jobs_{item_key}",
            imp_name=item.edge_weights,
        )
        .rename(f"jobs_{item_key}")
        .fillna(0),
        left_on=osmid_col,
        right_on="osmid",
        how="left",
    )

    return pd.DataFrame(
        df[["geometry"]]
        .reset_index(names="orig_index")
        .sjoin(mesh, how="left")
        .drop(columns=[osmid_col, "index_right", "geometry"])
        .groupby("orig_index")
        .mean()[f"jobs_{item_key}"]
    )


def calculate_aggregate(
    results: list[tuple[str, pd.Series]],
) -> dict:
    return pd.concat([result for _, result in results], axis=1).to_dict(orient="index")
