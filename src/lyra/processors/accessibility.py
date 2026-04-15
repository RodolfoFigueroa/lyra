import geopandas as gpd
from lyra.db import engine
from dataclasses import dataclass


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


def get_denue_from_bounds(
    xmin: float, ymin: float, xmax: float, ymax: float
) -> gpd.GeoDataFrame:
    with engine.connect() as conn:
        return gpd.read_postgis(
            """
            SELECT codigo_act, per_ocu, geometry
                FROM denue_05_2025
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


def load_denue_amenities(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = (
        df["geometry"].to_crs("EPSG:6372").buffer(10_000).total_bounds
    )
    denue_amenities = get_denue_from_bounds(xmin, ymin, xmax, ymax).assign(
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
