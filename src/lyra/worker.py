from lyra.registry import TASK_REGISTRY
import os
import json
import redis
from celery import Celery
from typing import Callable, Type
from types import FunctionType
from pydantic import BaseModel
from lyra.functions.load.db import load_geojson_from_cvegeos


REDIS_URL = os.environ["CELERY_BROKER_URL"]
celery_app = Celery("ee_tasks", broker=REDIS_URL, backend=REDIS_URL)
redis_client = redis.from_url(REDIS_URL)


def make_celery_wrapper(
    original_calculate_func: FunctionType,
    ModelClass: Type[BaseModel],
    conversion_params: list[str],
) -> Callable:
    def wrapper(self, validated_dict, *args, **kwargs):
        task_id = self.request.id

        try:
            # Inject geometries from CVEGEOs into the validated dict before reconstructing the model
            for param_name in conversion_params:
                validated_dict[param_name] = load_geojson_from_cvegeos(
                    validated_dict[param_name]
                )

            reconstructed_model = ModelClass(**validated_dict)

            # Preserve validated model
            func_kwargs = {
                field_name: getattr(reconstructed_model, field_name)
                for field_name in reconstructed_model.model_fields.keys()
            }
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
