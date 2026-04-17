import geopandas as gpd
import os
import json
import redis
from celery import Celery
from lyra.auth import initialize_earth_engine
import pkgutil
from typing import Callable
import importlib

initialize_earth_engine()

REDIS_URL = os.environ["CELERY_BROKER_URL"]
celery_app = Celery("ee_tasks", broker=REDIS_URL, backend=REDIS_URL)
redis_client = redis.from_url(REDIS_URL)


def convert_geojson_to_gdf(geojson: dict):
    return gpd.GeoDataFrame.from_features(
        geojson["features"],
        crs=geojson["crs"]["properties"]["name"],
    )


def make_celery_wrapper(calculate_f: Callable):
    def wrapper(self, geojson: dict):
        task_id = self.request.id

        try:
            gdf = convert_geojson_to_gdf(geojson)

            full_payload = {"status": "success", "result": calculate_f(gdf)}
            redis_client.setex(f"task_result_{task_id}", 600, json.dumps(full_payload))
            print(
                f"Dumped full payload to Redis for download in key: task_result_{task_id}"
            )

            notification_payload = {"status": "success", "download_id": task_id}
        except Exception as e:
            notification_payload = {"status": "error", "message": str(e)}

        channel_name = f"task_results_{task_id}"
        redis_client.publish(channel_name, json.dumps(notification_payload))

        return notification_payload

    wrapper.__name__ = getattr(calculate_f, "__name__")
    return wrapper


def register_tasks():
    import lyra.processors as processors

    for module_info in pkgutil.iter_modules(processors.__path__):
        module_name = module_info.name

        full_module_name = f"lyra.processors.{module_name}"
        module = importlib.import_module(full_module_name)

        if hasattr(module, "calculate") and callable(module.calculate):
            f = getattr(module, "calculate")
            f_wrapped = make_celery_wrapper(f)
            celery_app.task(name=module_name, bind=True)(f_wrapped)
            print(f"Registered task: {module_name}")


register_tasks()
