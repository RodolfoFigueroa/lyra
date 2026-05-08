import hashlib
import json
import logging
import os
from collections.abc import Callable
from types import FunctionType
from typing import Literal, cast

import redis
from celery import Celery, Task
from pydantic import BaseModel

from lyra.converters import converter_map
from lyra.registry import TASK_REGISTRY

REDIS_URL = os.environ["CELERY_BROKER_URL"]
logger = logging.getLogger(__name__)
celery_app = Celery("ee_tasks", broker=REDIS_URL, backend=REDIS_URL)
redis_client = redis.from_url(REDIS_URL)


def _has_met_zone_code(validated_dict: dict) -> bool:
    return any(
        isinstance(v, dict) and v.get("data_type") == "met_zone_code"
        for v in validated_dict.values()
    )


def _build_deterministic_cache_key(task_name: str, validated_dict: dict) -> str:
    serialised = json.dumps(validated_dict, sort_keys=True)
    digest = hashlib.sha256(serialised.encode()).hexdigest()
    return f"lyra_cache:{task_name}:{digest}"


def convert_explicit_type(
    payload: dict, *, request_type: Literal["location", "bounds"]
) -> dict:
    data = payload["value"]
    data_type = payload["data_type"]

    converter = converter_map[request_type][data_type]

    # Route to the correct conversion function based on data_type field
    raw_geojson = converter(data)

    # Repackage the processed GeoJSON into the wrapped format expected by the
    # reconstructed Pydantic model
    return {
        "data_type": "geojson",
        "value": raw_geojson,
    }


def rebuild_function_kwargs(reconstructed_model: BaseModel) -> dict:
    # Massage function kwargs to unwrap the GeoJSON from the discriminator
    # wrapper if necessary
    func_kwargs = {}
    for k in reconstructed_model.model_fields:
        attr = getattr(reconstructed_model, k)

        if hasattr(attr, "data_type") and hasattr(attr, "value"):
            func_kwargs[k] = attr.value
        else:
            func_kwargs[k] = attr
    return func_kwargs


def update_validated_dict_with_converted_types(
    validated_dict: dict,
    conversion_map: dict[str, list[str]],
) -> None:
    for param_name, tags in conversion_map.items():
        if "REQUIRE_EXPLICIT_TYPE" in tags:
            request_type = "location"
        elif "REQUIRE_EXPLICIT_BOUNDS_TYPE" in tags:
            request_type = "bounds"

        validated_dict[param_name] = convert_explicit_type(
            validated_dict[param_name], request_type=request_type
        )


def _resolve_cache(
    task_id: str, task_name: str, validated_dict: dict
) -> tuple[str | None, bool]:
    """Check the deterministic cache for a met_zone_code request.

    Returns (det_key, cache_hit). det_key is None when no parameter carries
    data_type='met_zone_code'. When cache_hit is True the cached payload has
    already been written to result_data_{task_id}.
    """
    if not _has_met_zone_code(validated_dict):
        return None, False
    det_key = _build_deterministic_cache_key(task_name, validated_dict)
    cached = redis_client.get(det_key)
    if cached is not None:
        cached_bytes = cast("bytes", cached)
        redis_client.setex(f"result_data_{task_id}", 600, cached_bytes)
        logger.info(
            "Celery task %s serving cached result for %s (key: %s)",
            task_id,
            task_name,
            det_key,
        )
        return det_key, True
    return det_key, False


def _store_result(task_id: str, result: dict, det_key: str | None) -> dict:
    full_payload = {"status": "success", "result": result}
    serialised_payload = json.dumps(full_payload)
    redis_client.setex(f"result_data_{task_id}", 600, serialised_payload)
    if det_key is not None:
        redis_client.setex(det_key, 86400, serialised_payload)
    return {"status": "success", "download_id": task_id}


def _handle_task_exception(task_id: str, metric_name: str, exc: Exception) -> dict:
    logger.exception(
        "Celery task %s failed while executing metric %s",
        task_id,
        metric_name,
    )
    return {"status": "error", "error_type": "worker", "message": str(exc)}


def _publish_notification(task_id: str, notification: dict) -> dict:
    channel_name = f"task_results_{task_id}"
    redis_client.publish(channel_name, json.dumps(notification))
    return notification


def make_celery_wrapper(
    original_calculate_func: FunctionType,
    ModelClass: type[BaseModel],  # noqa: N803
    conversion_map: dict[str, list[str]],
) -> Callable:
    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id
        task_name = str(self.name or original_calculate_func.__name__)
        det_key, cache_hit = _resolve_cache(task_id, task_name, validated_dict)
        if cache_hit:
            hit_notification = {"status": "success", "download_id": task_id}
            return _publish_notification(task_id, hit_notification)

        try:
            update_validated_dict_with_converted_types(validated_dict, conversion_map)
            reconstructed_model = ModelClass(**validated_dict)
            func_kwargs = rebuild_function_kwargs(reconstructed_model)
            result = original_calculate_func(**func_kwargs)
            notification = _store_result(task_id, result, det_key)
        except Exception as e:
            notification = _handle_task_exception(
                task_id, getattr(self, "name", original_calculate_func.__module__), e
            )

        return _publish_notification(task_id, notification)

    wrapper.__name__ = original_calculate_func.__name__
    return wrapper


def make_celery_wrapper_file(
    original_calculate_func: FunctionType,
    ModelClass: type[BaseModel],  # noqa: N803
    conversion_map: dict[str, list[str]],
) -> Callable:
    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id

        try:
            update_validated_dict_with_converted_types(validated_dict, conversion_map)
            reconstructed_model = ModelClass(**validated_dict)
            func_kwargs = rebuild_function_kwargs(reconstructed_model)
            file_path = original_calculate_func(**func_kwargs)
            full_payload = {
                "status": "success",
                "result_type": "file",
                "file_path": str(file_path),
            }
            redis_client.setex(f"result_data_{task_id}", 600, json.dumps(full_payload))
            notification = {"status": "success", "download_id": task_id}
        except Exception as e:
            notification = _handle_task_exception(
                task_id, getattr(self, "name", original_calculate_func.__module__), e
            )

        return _publish_notification(task_id, notification)

    wrapper.__name__ = original_calculate_func.__name__
    return wrapper


def make_celery_wrapper_batched(
    prepare_func: FunctionType,
    for_items_func: FunctionType,
    aggregate_func: FunctionType,
    ModelClass: type[BaseModel],  # noqa: N803
    conversion_map: dict[str, list[str]],
    items_default: dict,
) -> Callable:
    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id
        task_name = str(self.name or prepare_func.__name__)
        det_key, cache_hit = _resolve_cache(task_id, task_name, validated_dict)
        if cache_hit:
            hit_notification = {"status": "success", "download_id": task_id}
            return _publish_notification(task_id, hit_notification)

        try:
            update_validated_dict_with_converted_types(validated_dict, conversion_map)
            reconstructed_model = ModelClass(**validated_dict)
            func_kwargs = rebuild_function_kwargs(reconstructed_model)

            items_dict = func_kwargs.pop("items", None) or items_default
            if items_dict is None:
                err = (
                    "No items provided and no ITEMS_DEFAULT defined for this processor."
                )
                raise ValueError(err)

            prepared = prepare_func(**func_kwargs)
            results = [
                (key, for_items_func(key, item, **prepared))
                for key, item in items_dict.items()
            ]
            result = aggregate_func(results)
            notification = _store_result(task_id, result, det_key)
        except Exception as e:
            notification = _handle_task_exception(
                task_id, getattr(self, "name", prepare_func.__module__), e
            )

        return _publish_notification(task_id, notification)

    wrapper.__name__ = prepare_func.__name__
    return wrapper


def register_tasks() -> None:
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
        elif info["returns_file"]:
            wrapped_function = make_celery_wrapper_file(
                info["calculate"],
                info["model"],
                info["params_to_convert"],
            )
        else:
            wrapped_function = make_celery_wrapper(
                info["calculate"],
                info["model"],
                info["params_to_convert"],
            )
        celery_app.task(name=metric_name, bind=True)(wrapped_function)


register_tasks()
