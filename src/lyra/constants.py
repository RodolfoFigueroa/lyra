from dataclasses import dataclass

PER_OCU_TO_NUM_WORKERS_MAP = {
    "0 a 5 personas": 3,
    "6 a 10 personas": 8,
    "11 a 30 personas": 20,
    "31 a 50 personas": 40,
    "51 a 100 personas": 75,
    "101 a 250 personas": 175,
    "251 y más personas": 500,
}


YEAR_TO_DENUE_TABLE_MAP = {
    2020: "denue_2020_11",
    2021: "denue_2021_11",
    2022: "denue_2022_11",
    2023: "denue_2023_11",
    2024: "denue_2024_11",
    2025: "denue_2025_05",
}


@dataclass
class AmenityQuery:
    pob_query: str
    denue_query: str | None
    attraction_query: str
    radius: float
    importance: float


AMENITIES_DICT = {
    # Salud
    "salud_hospital": AmenityQuery(
        pob_query="pobtot",
        denue_query=r"^622",
        # Each worker can attend to 20 patients per day
        attraction_query="num_workers * 20",
        radius=5000,
        importance=0.1,
    ),
    "salud_consultorio": AmenityQuery(
        pob_query="pobtot",
        denue_query=r"^621",
        # Each worker can attend to 2 patients per hour, 8 hours a day
        attraction_query="num_workers * 2 * 8",
        radius=2000,
        importance=0.05,
    ),
    "salud_farmacia": AmenityQuery(
        pob_query="pobtot",
        denue_query=r"^46411",
        # Each worker fills 10 prescriptions per hour (daily average), 12 hours a day
        attraction_query="num_workers * 10 * 12",
        radius=1000,
        importance=0.05,
    ),
    # Recreativo
    "recreativo_parque": AmenityQuery(
        pob_query="pobtot",
        denue_query=None,
        # 30 m² per visitor, 2 turnover cycles per day (morning and afternoon/evening)
        attraction_query="area / 30 * 2",
        radius=3000,
        importance=0.05,
    ),
    "recreativo_club": AmenityQuery(
        pob_query="p_12a14 + pob15_64",
        denue_query=r"^(71391|71394)",
        attraction_query="num_workers * 50",
        radius=2000,
        importance=0.05,
    ),
    "recreativo_cine": AmenityQuery(
        pob_query="pobtot",
        denue_query=r"^51213",
        # 5 workers per screen, 5 movies per day, 25 visitors per movie
        attraction_query="num_workers / 5 * 5 * 25",
        radius=5000,
        importance=0.03,
    ),
    "recreativo_otro": AmenityQuery(
        pob_query="p_12a14 + pob15_64",
        denue_query=r"^(71399|712|713)",
        # Each worker can attend to 200 visitors per week, distributed across the week
        attraction_query="num_workers * 200 / 7",
        radius=3000,
        importance=0.02,
    ),
    # Educación
    "educacion_guarderia": AmenityQuery(
        pob_query="p_0a2 + p_3a5",
        denue_query=r"^6244",
        # Each worker can attend to 8 children per day
        attraction_query="num_workers * 8",
        radius=3000,
        importance=0.05,
    ),
    "educacion_preescolar": AmenityQuery(
        pob_query="p_3a5",
        denue_query=r"^61111",
        attraction_query="num_workers * 20",
        radius=3000,
        importance=0.15,
    ),
    "educacion_primaria": AmenityQuery(
        pob_query="p_6a11",
        denue_query=r"^61112",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "educacion_secundaria": AmenityQuery(
        pob_query="p_12a14",
        denue_query=r"^(61113|61114)",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "educacion_media_superior": AmenityQuery(
        pob_query="p_15a17",
        denue_query=r"^(61115|61116)",
        attraction_query="num_workers * 30",
        radius=3000,
        importance=0.15,
    ),
    "educacion_superior": AmenityQuery(
        pob_query="p_18a24",
        denue_query=r"^(6112|6113)",
        attraction_query="num_workers * 40",
        radius=3000,
        importance=0.15,
    ),
}

WALK_SPEED_KPH = 5  # Average walking speed in kilometers per hour