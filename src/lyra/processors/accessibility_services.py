from lyra.processors.common import load_geometries_from_bounds
import os
import numpy as np
import pandas as pd
import geopandas as gpd
from dataclasses import dataclass
from pathlib import Path
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
        denue_query='codigo_act.str.match("^622")',
        # Each worker can attend to 20 patients per day
        attraction_query="num_workers * 20",
        radius=5000,
        importance=0.1,
    ),
    "Consultorios médicos": AmenityQuery(
        pob_query="POBTOT",
        denue_query='codigo_act.str.match("^621")',
        # Each worker can attend to 2 patients per hour, 8 hours a day
        attraction_query="num_workers * 2 * 8",
        radius=2000,
        importance=0.05,
    ),
    "Farmacia": AmenityQuery(
        pob_query="POBTOT",
        denue_query='codigo_act.str.match("^46411")',
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
        denue_query='codigo_act.str.match("^(71391|71394)")',
        attraction_query="num_workers * 50",
        radius=2000,
        importance=0.05,
    ),
    "Cine": AmenityQuery(
        pob_query="POBTOT",
        denue_query='codigo_act.str.match("^51213")',
        # 5 workers per screen, 5 movies per day, 25 visitors per movie
        attraction_query="num_workers / 5 * 5 * 25",
        radius=5000,
        importance=0.03,
    ),
    "Otros servicios recreativos": AmenityQuery(
        pob_query="P_12A14 + POB15_64",
        denue_query='codigo_act.str.match("^(71399|712|713)")',
        # Each worker can attend to 200 visitors per week, distributed across the week
        attraction_query="num_workers * 200 / 7",
        radius=3000,
        importance=0.02,
    ),
    # Educación
    "Guarderia": AmenityQuery(
        pob_query="P_0A2 + P_3A5",
        denue_query='codigo_act.str.match("^6244")',
        # Each worker can attend to 8 children per day
        attraction_query="num_workers * 8",
        radius=3000,
        importance=0.05,
    ),
    "Educación preescolar": AmenityQuery(
        pob_query="P_3A5",
        denue_query='codigo_act.str.match("^61111")',
        attraction_query="num_workers * 20",
        radius=3000,
        importance=0.15,
    ),
    "Educación primaria": AmenityQuery(
        pob_query="P_6A11",
        denue_query='codigo_act.str.match("^61112")',
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación secundaria": AmenityQuery(
        pob_query="P_12A14",
        denue_query='codigo_act.str.match("^(61113|61114)")',
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación media superior": AmenityQuery(
        pob_query="P_15A17",
        denue_query='codigo_act.str.match("^(61115|61116)")',
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "Educación superior": AmenityQuery(
        pob_query="P_18A24",
        denue_query='codigo_act.str.match("^(6112|6113)")',
        attraction_query="num_workers * 40",
        radius=3000,
        importance=0.15,
    ),
}


def load_denue_amenities(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = (
        df["geometry"].to_crs("EPSG:6372").buffer(10_000).total_bounds
    )
    denue_amenities = load_geometries_from_bounds(
        xmin,
        ymin,
        xmax,
        ymax,
        columns=["codigo_act", "per_ocu", "geometry"],
        table_name="denue_05_2025",
    ).assign(
        num_workers=lambda x: x["per_ocu"].map(
            {
                "0 a 5 personas": 3,
                "6 a 10 personas": 8,
                "11 a 30 personas": 20,
                "31 a 50 personas": 40,
                "51 a 100 personas": 75,
                "101 a 250 personas": 175,
                "251 y más personas": 500,
            }
        ),
        codigo_act=lambda x: x["codigo_act"].astype(str),
    )[["geometry", "codigo_act", "num_workers"]]

    for name, amenity_query in AMENITIES_DICT.items():
        query = amenity_query.denue_query
        if query is None:
            continue

        idx = denue_amenities.query(query).index
        denue_amenities.loc[idx, "amenity"] = name

    denue_amenities = denue_amenities.dropna(subset=["amenity"]).drop(
        columns=["codigo_act"],
    )

    return denue_amenities


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


def load_mesh_roads(agebs, mesh_in_path, mesh_opath, nodes_path, edges_path):
    if mesh_opath.exists() and nodes_path.exists() and edges_path.exists():
        print("Loading cache")
        mesh = gpd.read_parquet(mesh_opath)
        nodes = (
            gpd.read_file(nodes_path)
            .set_index("osmid")
            .to_crs("EPSG:6372")
            .filter(["geometry"])
        )
        edges = gpd.read_file(edges_path).to_crs("EPSG:6372")[["u", "v", "length"]]
        return mesh, nodes, edges

    print("Calculating from scratch")
    # Add population data to mesh
    mesh = merge_mesh_census(agebs, mesh_in_path)

    # Load road network
    bounds = tuple(
        agebs.to_crs("EPSG:4326").total_bounds,
    )
    nodes, edges = load_roads(bounds, nodes_path, edges_path)
    nodes = nodes.to_crs(agebs.crs)

    # Assign nodes to origins in the mesh
    # A single node per cell, the closest to cell centroid
    # Some cells have zero nodes, and are thus inaccesssible
    mesh_osmid = (
        mesh[["CODIGO", "geometry"]]
        .sjoin(nodes, how="inner", predicate="contains")
        .merge(nodes, on="osmid")
        .assign(cent_dist=lambda df: df.geometry_x.centroid.distance(df.geometry_y))
        .loc[lambda df: df.groupby("CODIGO").cent_dist.idxmin()][["CODIGO", "osmid"]]
    )

    mesh = mesh.merge(mesh_osmid, how="left", on="CODIGO")
    mesh.to_parquet(mesh_opath)
    return mesh, nodes, edges


def compute_accessibility_services(
    amenities: gpd.GeoDataFrame,
    agebs: gpd.GeoDataFrame,
    mesh: gpd.GeoDataFrame,
    nodes: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    access_path: os.PathLike | None = None,
):
    if access_path is not None:
        access_path = Path(access_path)
        if access_path.exists():
            return pd.read_parquet(access_path)

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

    # Aggregate over agebs
    df_access = (
        gpd.GeoDataFrame(
            agebs[["geometry"]]
            .sjoin(mesh[["geometry", "accessibility_score"]], how="left")
            .groupby(["ENTIDAD", "MUN", "LOC", "AGEB"])
            .agg({"accessibility_score": "mean"}),
            # crs=mesh.crs,
        )
        .reset_index()
        .rename(columns={"accessibility_score": "accessibility"})
        .set_index(["ENTIDAD", "MUN", "LOC", "AGEB"])
    )

    if access_path is not None:
        df_access.to_parquet(access_path)

    return df_access


def calculate(df: gpd.GeoDataFrame, df_public_spaces: gpd.GeoDataFrame) -> dict:
    df_denue = load_denue_amenities(df)
    df_amenities = concat_amenities(df_denue, df_public_spaces)
    return compute_accessibility_services(
        df_amenities,
    )
