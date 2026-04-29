from lyra.registry import TASK_REGISTRY
import os
import json
import logging
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
logger = logging.getLogger(__name__)
celery_app = Celery("ee_tasks", broker=REDIS_URL, backend=REDIS_URL)
redis_client = redis.from_url(REDIS_URL)


def make_celery_wrapper(
    original_calculate_func: FunctionType,
    ModelClass: Type[BaseModel],
    conversion_map: dict[str, list[str]],
) -> Callable:
    def wrapper(self, validated_dict, *args, **kwargs):
        processor_map = {
            "cvegeo_list": load_geojson_from_cvegeos,
            "met_zone_name": load_geojson_from_met_zone_name,
            "geojson": lambda x: x,
        }

        task_id = self.request.id

        try:
            for param_name, tags in conversion_map.items():
                if "REQUIRE_EXPLICIT_TYPE" in tags:
                    payload = validated_dict[param_name]

                    data_type = payload["data_type"]
                    data = payload["value"]

                    # Route to the correct conversion function based on data_type field
                    raw_geojson = processor_map[data_type](data)

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
            logger.exception(
                "Celery task %s failed while executing metric %s",
                task_id,
                getattr(self, "name", original_calculate_func.__module__),
            )
            notification = {"status": "error", "message": str(e)}

        # Publish the notification
        channel_name = f"task_results_{task_id}"
        redis_client.publish(channel_name, json.dumps(notification))
        return notification

    wrapper.__name__ = original_calculate_func.__name__
    return wrapper


def make_celery_wrapper_batched(
    prepare_func: FunctionType,
    for_items_func: FunctionType,
    aggregate_func: FunctionType,
    ModelClass: Type[BaseModel],
    conversion_map: dict[str, list[str]],
    items_default,
) -> Callable:
    def wrapper(self, validated_dict, *args, **kwargs):
        processor_map = {
            "cvegeo_list": load_geojson_from_cvegeos,
            "met_zone_name": load_geojson_from_met_zone_name,
            "geojson": lambda x: x,
        }

        task_id = self.request.id

        try:
            for param_name, tags in conversion_map.items():
                if "REQUIRE_EXPLICIT_TYPE" in tags:
                    payload = validated_dict[param_name]

                    data_type = payload["data_type"]
                    data = payload["value"]

                    raw_geojson = processor_map[data_type](data)

                    validated_dict[param_name] = {
                        "data_type": "geojson",
                        "value": raw_geojson,
                    }

            reconstructed_model = ModelClass(**validated_dict)

            func_kwargs = {}
            for k in reconstructed_model.model_fields.keys():
                attr = getattr(reconstructed_model, k)

                if hasattr(attr, "data_type") and hasattr(attr, "value"):
                    func_kwargs[k] = attr.value
                else:
                    func_kwargs[k] = attr

            items_dict = func_kwargs.pop("items", None) or items_default
            if items_dict is None:
                raise ValueError(
                    "No items provided and no ITEMS_DEFAULT defined for this processor."
                )

            prepared = prepare_func(**func_kwargs)

            results = [
                (key, for_items_func(key, item, **prepared))
                for key, item in items_dict.items()
            ]

            result = aggregate_func(results)

            full_payload = {"status": "success", "result": result}
            redis_client.setex(f"result_data_{task_id}", 600, json.dumps(full_payload))

            notification = {"status": "success", "download_id": task_id}

        except Exception as e:
            logger.exception(
                "Celery task %s failed while executing metric %s",
                task_id,
                getattr(self, "name", prepare_func.__module__),
            )
            notification = {"status": "error", "message": str(e)}

        channel_name = f"task_results_{task_id}"
        redis_client.publish(channel_name, json.dumps(notification))
        return notification

    wrapper.__name__ = prepare_func.__name__
    return wrapper


def register_tasks():
    for metric_name, info in TASK_REGISTRY.items():
        if info["is_batched"]:
            wrapped_function = make_celery_wrapper_batched(
                info["calculate_prepare"],
                info["calculate_for_items"],
                info["calculate_aggregate"],
                info["model"],
                info["params_to_convert"],
                info["items_default"],
            )
        else:
            wrapped_function = make_celery_wrapper(
                info["calculate"], info["model"], info["params_to_convert"]
            )
        celery_app.task(name=metric_name, bind=True)(wrapped_function)


register_tasks()
