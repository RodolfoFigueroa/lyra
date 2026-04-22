from lyra.models import GeoJSON, StrictBaseModel
from lyra.constants import PER_OCU_TO_NUM_WORKERS_MAP
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
import geopandas as gpd
from dataclasses import dataclass
import pandana as pdna


@dataclass
class AmenityQuery:
    pob_query: str
    denue_query: str | None
    attraction_query: str
    radius: float
    importance: float


AMENITIES_DICT = {
    # Salud
    "Hospital general": AmenityQuery(
        pob_query="POBTOT",
        denue_query=r"^622",
        # Each worker can attend to 20 patients per day
        attraction_query="num_workers * 20",
        radius=5000,
        importance=0.1,
    ),
    "Consultorios médicos": AmenityQuery(
        pob_query="POBTOT",
        denue_query=r"^621",
        # Each worker can attend to 2 patients per hour, 8 hours a day
        attraction_query="num_workers * 2 * 8",
        radius=2000,
        importance=0.05,
    ),
    "Farmacia": AmenityQuery(
        pob_query="POBTOT",
        denue_query=r"^46411",
        # Each worker fills 10 prescriptions per hour (daily average), 12 hours a day
        attraction_query="num_workers * 10 * 12",
        radius=1000,
        importance=0.05,
    ),
    # Recreativo
    "Parques recreativos": AmenityQuery(
        pob_query="POBTOT",
        denue_query=None,
        # 30 m² per visitor, 2 turnover cycles per day (morning and afternoon/evening)
        attraction_query="area / 30 * 2",
        radius=3000,
        importance=0.05,
    ),
    "Clubs deportivos y de acondicionamiento físico": AmenityQuery(
        pob_query="P_12A14 + POB15_64",
        denue_query=r"^(71391|71394)",
        attraction_query="num_workers * 50",
        radius=2000,
        importance=0.05,
    ),
    "Cine": AmenityQuery(
        pob_query="POBTOT",
        denue_query=r"^51213",
        # 5 workers per screen, 5 movies per day, 25 visitors per movie
        attraction_query="num_workers / 5 * 5 * 25",
        radius=5000,
        importance=0.03,
    ),
    "Otros servicios recreativos": AmenityQuery(
        pob_query="P_12A14 + POB15_64",
        denue_query=r"^(71399|712|713)",
        # Each worker can attend to 200 visitors per week, distributed across the week
        attraction_query="num_workers * 200 / 7",
        radius=3000,
        importance=0.02,
    ),
    # Educación
    "Guarderia": AmenityQuery(
        pob_query="P_0A2 + P_3A5",
        denue_query=r"^6244",
        # Each worker can attend to 8 children per day
        attraction_query="num_workers * 8",
        radius=3000,
        importance=0.05,
    ),
    "Educación preescolar": AmenityQuery(
        pob_query="P_3A5",
        denue_query=r"^61111",
        attraction_query="num_workers * 20",
        radius=3000,
        importance=0.15,
    ),
    "Educación primaria": AmenityQuery(
        pob_query="P_6A11",
        denue_query=r"^61112",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación secundaria": AmenityQuery(
        pob_query="P_12A14",
        denue_query=r"^(61113|61114)",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación media superior": AmenityQuery(
        pob_query="P_15A17",
        denue_query=r"^(61115|61116)",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación superior": AmenityQuery(
        pob_query="P_18A24",
        denue_query=r"^(6112|6113)",
        attraction_query="num_workers * 40",
        radius=3000,
        importance=0.15,
    ),
}


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
        if c in {"area_fraction", "CODIGO"}:
            continue
        mesh_agg[c] = mesh_agg[c] * mesh_agg["area_fraction"]
    mesh_agg = mesh_agg.drop(columns="area_fraction").groupby("CODIGO").sum()
    return mesh.merge(mesh_agg, on="CODIGO", how="left").fillna(0.0)


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


def compute_accessibility_services(
    df: gpd.GeoDataFrame,
    amenities: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
):
    net_accessibility = pdna.Network(
        nodes["geometry"].x.copy(),
        nodes["geometry"].y.copy(),
        edges["u"].copy(),
        edges["v"].copy(),
        edges[["length"]].copy(),
    )

    # Assign POIS to network
    for amenity_type in amenities.amenity.unique():
        to_gdf = amenities[amenities["amenity"] == amenity_type]
        net_accessibility.set_pois(
            category=amenity_type,
            x_col=to_gdf["geometry"].centroid.x,
            y_col=to_gdf["geometry"].centroid.y,
            maxdist=200000,
            maxitems=5,
            mapping_distance=1000,
        )

    # Add destination properties
    amenities["osmid"] = net_accessibility.get_node_ids(
        x_col=amenities["geometry"].centroid.x,
        y_col=amenities["geometry"].centroid.y,
        mapping_distance=1000,
    )

    # Set node properties of destinations
    mesh_osmid = mesh[mesh.osmid.notna()]
    for c in mesh.columns:
        if not c.startswith("P"):
            continue
        net_accessibility.set(mesh_osmid["osmid"], variable=mesh_osmid[c], name=c)

    # Calculate aggregations for population reached for each category
    for c in mesh.columns:
        if not c.startswith("P"):
            continue
        amenities = amenities.merge(
            net_accessibility.aggregate(1000, "sum", "exp", name=c).rename(c),
            on="osmid",
            how="left",
        )

    # Find reached population relevant for each amenity type
    amenities["reached_population"] = 0.0
    for amenity_type in amenities.amenity.unique():
        query = AMENITIES_DICT[amenity_type].pob_query
        amenities.loc[amenities["amenity"] == amenity_type, "reached_population"] = (
            amenities.loc[amenities["amenity"] == amenity_type].eval(query)
        )

    # Adjust attraction by discounting opportunities taken by reached population
    amenities["adj_attraction"] = (
        amenities.attraction
        / amenities.reached_population.where(amenities.reached_population > 1, 1)
    )

    # We need to aggregate adjusted attraction for a single node
    destinations = amenities.groupby("osmid").adj_attraction.sum()

    # Aggregate adjusted attraction for origin nodes
    # Set node properties of destinations
    net_accessibility.set(destinations.index, variable=destinations.values, name="attr")

    # Aggregate origin nodes
    mesh = mesh.merge(
        net_accessibility.aggregate(1000, "sum", "exp", name="attr").rename(
            "accessibility"
        ),
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
    return (
        gpd.GeoDataFrame(
            df[["geometry"]]
            .sjoin(mesh[["geometry", "accessibility_score"]], how="left")
            .groupby(["ENTIDAD", "MUN", "LOC", "AGEB"])
            .agg({"accessibility_score": "mean"}),
        )
        .reset_index()
        .rename(columns={"accessibility_score": "accessibility"})
        .set_index(["ENTIDAD", "MUN", "LOC", "AGEB"])
    )


def calculate(df: gpd.GeoDataFrame, df_public_spaces: gpd.GeoDataFrame | None) -> dict:
    df = df.to_crs("EPSG:6372")
    xmin, ymin, xmax, ymax = df["geometry"].buffer(10_000).total_bounds

    if df_public_spaces is None:
        df_public_spaces = load_osm_features_from_bounds(
            xmin, ymin, xmax, ymax, bounds_crs="EPSG:6372", tags={"leisure": ["park"]}
        )

    df_denue_base = load_denue_from_bounds(xmin, ymin, xmax, ymax)
    df_denue = process_denue_amenities(df_denue_base)
    df_amenities = concat_amenities(df_denue, df_public_spaces)

    nodes, edges = load_roads_from_bounds(
        xmin, ymin, xmax, ymax, bounds_crs="EPSG:6372"
    )

    df_agebs = load_census_from_bounds(
        xmin, ymin, xmax, ymax, level="ageb", columns=["cvegeo", "pobtot"]
    )

    df_mesh = (
        load_mesh_from_bounds(xmin, ymin, xmax, ymax, level=9)
        .pipe(merge_mesh_and_census, agebs=df_agebs)
        .assign(osmid=lambda df: get_osmid_from_nodes(df, nodes))
    )

    return compute_accessibility_services(df, df_amenities, df_mesh, nodes, edges)


class RequestModel(StrictBaseModel):
    geojson: GeoJSON
    geojson_public: GeoJSON | None = None
