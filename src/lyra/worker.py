from lyra.registry import TASK_REGISTRY
import os
import json
import redis
from celery import Celery
from typing import Callable, Type
from types import FunctionType
from pydantic import BaseModel
from lyra.functions.load.db import (
    load_geojson_from_cvegeos,
    load_geojson_from_met_zone_name,
)


REDIS_URL = os.environ["CELERY_BROKER_URL"]
celery_app = Celery("ee_tasks", broker=REDIS_URL, backend=REDIS_URL)
redis_client = redis.from_url(REDIS_URL)


def make_celery_wrapper(
    original_calculate_func: FunctionType,
    ModelClass: Type[BaseModel],
    conversion_map: dict[str, list[str]],
) -> Callable:
    def wrapper(self, validated_dict, *args, **kwargs):
        task_id = self.request.id

        try:
            for param_name, tags in conversion_map.items():
                if "REQUIRE_EXPLICIT_TYPE" in tags:
                    payload = validated_dict[param_name]

                    data_type = payload.get("data_type")
                    data = payload.get("value")

                    # Route to the correct conversion function based on data_type field
                    if data_type == "cvegeo_list":
                        raw_geojson = load_geojson_from_cvegeos(data)

                    elif data_type == "met_zone_name":
                        raw_geojson = load_geojson_from_met_zone_name(data)

                    elif data_type == "geojson":
                        raw_geojson = data
                    else:
                        err = f"Unsupported explicit type: {data_type}"
                        raise ValueError(err)

                    # Repackage the processed GeoJSON into the wrapped format expected by the reconstructed Pydantic model
                    validated_dict[param_name] = {
                        "data_type": "geojson",
                        "value": raw_geojson,
                    }

            reconstructed_model = ModelClass(**validated_dict)

            # Massage function kwargs to unwrap the GeoJSON from the discriminator wrapper if necessary
            func_kwargs = {}
            for k in reconstructed_model.model_fields.keys():
                attr = getattr(reconstructed_model, k)

                if hasattr(attr, "data_type") and hasattr(attr, "value"):
                    func_kwargs[k] = attr.value
                else:
                    func_kwargs[k] = attr

            result = original_calculate_func(**func_kwargs)

            full_payload = {"status": "success", "result": result}
            redis_client.setex(f"result_data_{task_id}", 600, json.dumps(full_payload))

            notification = {"status": "success", "download_id": task_id}

        except Exception as e:
            notification = {"status": "error", "message": str(e)}

        # Publish the notification
        channel_name = f"task_results_{task_id}"
        redis_client.publish(channel_name, json.dumps(notification))
        return notification

    wrapper.__name__ = original_calculate_func.__name__
    return wrapper


def register_tasks():
    for metric_name, info in TASK_REGISTRY.items():
        wrapped_function = make_celery_wrapper(
            info["calculate"], info["model"], info["params_to_convert"]
        )
        celery_app.task(name=metric_name, bind=True)(wrapped_function)


register_tasks()
