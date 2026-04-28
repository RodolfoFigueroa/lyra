from typing import Annotated, Literal
from pydantic import Field
from lyra.models.base import GeoJSON
from lyra.models.wrappers import ExplicitLocationAPI
from lyra.constants import PER_OCU_TO_NUM_WORKERS_MAP
from lyra.functions.utils import convert_geojson_to_gdf
from lyra.models.processors.accessibility_services import AmenityGroupModel
from lyra.functions.load.db import (
    load_denue_from_bounds,
    load_mesh_from_bounds,
    load_census_from_bounds,
)
from lyra.functions.load.osm import (
    load_roads_from_bounds,
    load_osm_features_from_bounds,
)
import numpy as np
import pandas as pd
from lyra.constants import AMENITIES_DICT
import geopandas as gpd
import pandana as pdna


LENGTH_METERS_TO_TRAVEL_TIME_SECONDS_MULTIPLIER = (
    1 / (50 * 1000 / 3600)
)  # Assuming an average speed of 50 km/h, convert length in meters to travel time in seconds


def process_denue_amenities(df_denue: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    df_denue = df_denue.assign(
        num_workers=lambda x: x["per_ocu"].map(PER_OCU_TO_NUM_WORKERS_MAP)
    ).drop(columns=["per_ocu"])

    for name, amenity_query in AMENITIES_DICT.items():
        query = amenity_query.denue_query
        if query is None:
            continue
        df_denue.loc[lambda df: df["codigo_act"].str.match(query), "amenity"] = name

    return df_denue.dropna(subset=["amenity"]).drop(
        columns=["codigo_act"],
    )


def concat_amenities(
    df_denue: gpd.GeoDataFrame, df_public_spaces: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    df = (
        pd.concat(
            [df_denue, df_public_spaces],
            axis=0,
            ignore_index=True,
        )
        .assign(attraction=0.0)
        .pipe(lambda df: gpd.GeoDataFrame(df, geometry="geometry", crs=df.crs))
    )

    for amenity_type in df["amenity"].unique():
        df.loc[df.amenity == amenity_type, "attraction"] = df.loc[
            df.amenity == amenity_type
        ].eval(AMENITIES_DICT[amenity_type].attraction_query)

    return df


def merge_mesh_and_census(
    mesh: gpd.GeoDataFrame,
    agebs: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    crs = agebs.crs
    if crs is None:
        err = "AGEBs GeoDataFrame must have a defined CRS."
        raise ValueError(err)

    mesh = mesh.to_crs(crs)
    mesh_agg = (
        mesh.overlay(agebs.assign(ageb_area=lambda df: df.area))
        .assign(
            area_fraction=lambda df: df.area / df.ageb_area,
        )
        .drop(columns=["ageb_area", "geometry"])
    )
    for c in mesh_agg.columns:
        if c in {"area_fraction", "codigo"}:
            continue
        mesh_agg[c] = mesh_agg[c] * mesh_agg["area_fraction"]
    mesh_agg = mesh_agg.drop(columns="area_fraction").groupby("codigo").sum()
    return mesh.merge(mesh_agg, on="codigo", how="left").fillna(0.0)


def get_osmid_from_nodes(mesh: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> pd.Series:
    return (
        mesh[["geometry"]]
        .reset_index(names="index")
        .sjoin(nodes, how="inner", predicate="contains")
        .merge(nodes, on="osmid")
        .assign(cent_dist=lambda df: df.geometry_x.centroid.distance(df.geometry_y))
        .loc[lambda df: df.groupby("index").cent_dist.idxmin()]
        .set_index("index")["osmid"]
    )


def generate_accessibility_net(
    mesh: gpd.GeoDataFrame,
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
) -> pdna.Network:
    net_accessibility = pdna.Network(
        nodes["geometry"].x.copy(),
        nodes["geometry"].y.copy(),
        edges["u"].copy(),
        edges["v"].copy(),
        edges[["length", "travel_time"]].copy(),
    )

    # Set node properties of destinations
    mesh_osmid = mesh[mesh["osmid"].notna()]
    for c in mesh.columns:
        if not c.startswith("p"):
            continue
        net_accessibility.set(mesh_osmid["osmid"], variable=mesh_osmid[c], name=c)

    return net_accessibility


def get_amenities_attraction_and_osmid(
    net_accessibility: pdna.Network,
    amenities: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    *,
    weight_is_travel_time: bool,
    max_dist_meters: float,
) -> pd.DataFrame:
    if weight_is_travel_time:
        scale = LENGTH_METERS_TO_TRAVEL_TIME_SECONDS_MULTIPLIER
        impedance = "travel_time"
    else:
        scale = 1.0
        impedance = "length"

    # Add destination properties
    amenities = amenities.assign(
        osmid=lambda df: net_accessibility.get_node_ids(
            x_col=df["geometry"].centroid.x,
            y_col=df["geometry"].centroid.y,
            # Despite what pandana documentation says, this mapping distance is
            # just standard Euclidean, not based on network impedance. Thus, we
            # don't need to scale it.
            mapping_distance=1000,
        )
    )

    # Calculate aggregations for population reached for each category
    for c in mesh.columns:
        if not c.startswith("p"):
            continue
        aggregated = (
            net_accessibility.aggregate(
                max_dist_meters * scale,
                type="sum",
                decay="exp",
                name=c,
                imp_name=impedance,
            )
            .rename(c)
            .reset_index()
        )

        if aggregated["osmid"].duplicated().any():
            err = f"Duplicated osmids found in aggregated accessibility network for column {c}. This should never happen."
            raise ValueError(err)

        amenities = (
            amenities.reset_index(names="amenity_index")
            .merge(
                aggregated,
                on="osmid",
                how="left",
            )
            .set_index("amenity_index")
        )

    # Find reached population relevant for each amenity type
    amenities = amenities.assign(reached_population=0.0)
    for amenity_type in amenities["amenity"].unique():
        query = AMENITIES_DICT[amenity_type].pob_query
        amenities.loc[
            lambda df: df["amenity"] == amenity_type, "reached_population"
        ] = amenities.loc[lambda df: df["amenity"] == amenity_type].eval(query)

    # Adjust attraction by discounting opportunities taken by reached population
    return amenities.assign(
        adj_attraction=lambda df: (
            df["attraction"]
            / df["reached_population"].where(df["reached_population"] > 1, 1)
        )
    )[["osmid", "adj_attraction"]]


def compute_accessibility_services(
    df: gpd.GeoDataFrame,
    amenities: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    net_accessibility: pdna.Network,
    *,
    weight_is_travel_time: bool,
    max_dist_meters: float,
) -> pd.Series:
    if weight_is_travel_time:
        scale = LENGTH_METERS_TO_TRAVEL_TIME_SECONDS_MULTIPLIER
        impedance = "travel_time"
    else:
        scale = 1.0
        impedance = "length"

    # We need to aggregate adjusted attraction for a single node
    destinations = amenities.groupby("osmid")["adj_attraction"].sum()

    # Set node properties of destinations
    net_accessibility.set(destinations.index, variable=destinations.values, name="attr")

    # Aggregate origin nodes
    mesh = mesh.merge(
        net_accessibility.aggregate(
            max_dist_meters * scale,
            type="sum",
            decay="exp",
            name="attr",
            imp_name=impedance,
        ).rename("accessibility"),
        on="osmid",
        how="left",
    )

    # Create a score between 0 and 100 that is easy to compare.
    # Why are raw scores so bad? This should not be the case.
    mesh = mesh.assign(
        accessibility_score=lambda df: (
            (np.log(df["accessibility"].fillna(0.0) + 1) * 12.5).clip(0, 100) / 100
        ),
    )

    # Aggregate over geometries
    return gpd.GeoDataFrame(
        df[["geometry"]]
        .reset_index(names="index")
        .sjoin(mesh[["geometry", "accessibility_score"]], how="left")
        .groupby("index")
        .agg({"accessibility_score": "mean"}),
    ).rename(columns={"accessibility_score": "accessibility"})["accessibility"]


def process_amenities_and_compute_accessibility(
    df: gpd.GeoDataFrame,
    df_amenities: gpd.GeoDataFrame,
    df_mesh: gpd.GeoDataFrame,
    net_accessibility: pdna.Network,
    *,
    weight_is_travel_time: bool,
    max_dist_meters: float,
) -> pd.Series:
    temp = get_amenities_attraction_and_osmid(
        net_accessibility,
        df_amenities,
        df_mesh,
        weight_is_travel_time=weight_is_travel_time,
        max_dist_meters=max_dist_meters,
    )

    df_amenities = df_amenities.join(temp)

    return compute_accessibility_services(
        df,
        df_amenities,
        df_mesh,
        net_accessibility,
        weight_is_travel_time=weight_is_travel_time,
        max_dist_meters=max_dist_meters,
    )


def calculate_for_single_amenity_group(
    df: gpd.GeoDataFrame,
    df_amenities: gpd.GeoDataFrame,
    df_mesh: gpd.GeoDataFrame,
    net_accessibility: pdna.Network,
    edge_weights: Literal["length", "travel_time"],
    max_weight: float,
) -> pd.Series:
    weight_is_travel_time = edge_weights == "travel_time"

    # Always use the threshold in meters, since each function that uses the threshold will convert it to travel time if needed
    if edge_weights == "length":
        max_dist_meters = max_weight
    else:
        # In this case max_weight is in seconds
        max_dist_meters = max_weight / LENGTH_METERS_TO_TRAVEL_TIME_SECONDS_MULTIPLIER

    return process_amenities_and_compute_accessibility(
        df,
        df_amenities,
        df_mesh,
        net_accessibility,
        weight_is_travel_time=weight_is_travel_time,
        max_dist_meters=max_dist_meters,
    )


METRIC_DESCRIPTION: str = "Computes service accessibility scores for each spatial unit using road network analysis and amenity data."


def calculate(
    data: ExplicitLocationAPI,
    data_public: GeoJSON | None = None,
    amenity_groups: dict[Annotated[str, Field(max_length=64)], AmenityGroupModel] | None = None,
    year: Literal[2020, 2021, 2022, 2023, 2024, 2025] | None = None,
) -> dict:
    if amenity_groups is None:
        amenity_groups = {
            "default": AmenityGroupModel(
                edge_weights="length",
                max_weight=1000,
            )
        }

    wanted_crs = "EPSG:6372"

    if year is None:
        year = 2025

    df = convert_geojson_to_gdf(data).to_crs(wanted_crs)
    xmin, ymin, xmax, ymax = df["geometry"].buffer(10_000).total_bounds

    if data_public is None:
        df_public_spaces = load_osm_features_from_bounds(
            xmin, ymin, xmax, ymax, bounds_crs=wanted_crs, tags={"leisure": ["park"]}
        )
    else:
        df_public_spaces = (
            convert_geojson_to_gdf(data_public)[["area", "geometry"]]
            .to_crs(wanted_crs)
            .assign(amenity="recreativo_parque")
        )

    df_denue = process_denue_amenities(
        load_denue_from_bounds(xmin, ymin, xmax, ymax, year=year)
    )
    df_amenities = concat_amenities(df_denue, df_public_spaces)

    nodes, edges = load_roads_from_bounds(xmin, ymin, xmax, ymax, bounds_crs=wanted_crs)

    df_agebs = load_census_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        level="ageb",
        columns=[
            "pobtot",
            "p_0a2",
            "p_3a5",
            "p_6a11",
            "p_12a14",
            "p_15a17",
            "p_18a24",
            "pob15_64",
        ],
    )

    df_mesh = (
        load_mesh_from_bounds(xmin, ymin, xmax, ymax, level=9)
        .pipe(merge_mesh_and_census, agebs=df_agebs)
        .assign(osmid=lambda df: get_osmid_from_nodes(df, nodes))
    )

    net_accessibility = generate_accessibility_net(
        df_mesh,
        nodes,
        edges,
    )

    cols = []
    for key, group in amenity_groups.items():
        group_amenities = [
            amenity.value for amenity in group.amenities
        ]
        df_amenities_group = df_amenities.loc[
            lambda df: df["amenity"].isin(group_amenities)
        ]

        if len(df_amenities_group) == 0:
            err = f"No amenities found for group {key} with categories {group_amenities}"
            raise ValueError(err)

        accessibility_group = calculate_for_single_amenity_group(
            df,
            df_amenities_group,
            df_mesh,
            net_accessibility,
            edge_weights=group.edge_weights,
            max_weight=group.max_weight,
        )
        cols.append(accessibility_group.rename(f"accessibility_{key}"))

    return pd.concat(cols, axis=1).to_dict(orient="index")
