import geopandas as gpd
from pyproj import CRS
from lyra.models import GeoJSON


def convert_features_to_gdf(features, crs: CRS | str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame.from_features(
        features,
        crs=crs,
    )


def convert_geojson_to_gdf(geojson: GeoJSON) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame.from_features(
        geojson.features,
        crs=geojson.crs.properties.name,
    )
