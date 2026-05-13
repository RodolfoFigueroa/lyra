import geopandas as gpd
from lyra.sdk.models import GeoJSON, SingleGeoJSON


def convert_geojson_to_gdf(geojson: GeoJSON | SingleGeoJSON) -> gpd.GeoDataFrame:
    out = gpd.GeoDataFrame.from_features(
        [feature.model_dump(mode="json") for feature in geojson.features],
        crs=geojson.crs.properties.name,
    )
    out.index = [feature.id for feature in geojson.features]

    return out
