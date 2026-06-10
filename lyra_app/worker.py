import hashlib
import json
import logging
import time
from collections.abc import Callable
from types import FunctionType
from typing import Literal, cast

from celery import Celery, Task
from pydantic import BaseModel

from lyra_app.converters import converter_map
from lyra_app.db.redis import redis_client_sync, redis_url
from lyra_app.registry import TASK_REGISTRY

logger = logging.getLogger(__name__)
celery_app = Celery("ee_tasks", broker=redis_url, backend=redis_url)


def _has_met_zone_code(validated_dict: dict) -> bool:
    """Return whether any parameter in the request payload is a met_zone_code.

    Args:
        validated_dict (dict): The deserialised task request payload.

    Returns:
        bool: ``True`` if at least one value is a dict with
        ``data_type == "met_zone_code"``, ``False`` otherwise.
    """
    return any(
        isinstance(v, dict) and v.get("data_type") == "met_zone_code"
        for v in validated_dict.values()
    )


def _build_deterministic_cache_key(task_name: str, validated_dict: dict) -> str:
    """Build a stable Redis key for deterministic caching of a task request.

    The key is derived from a SHA-256 digest of the JSON-serialised (sorted)
    request payload, ensuring identical inputs always map to the same key.

    Args:
        task_name (str): The registered Celery task name.
        validated_dict (dict): The deserialised task request payload.

    Returns:
        str: A Redis key of the form ``lyra_cache:{task_name}:{sha256hex}``.
    """
    serialised = json.dumps(validated_dict, sort_keys=True)
    digest = hashlib.sha256(serialised.encode()).hexdigest()
    return f"lyra_cache:{task_name}:{digest}"


def convert_explicit_type(
    payload: dict,
    *,
    request_type: Literal["location", "bounds"],
) -> dict:
    """Convert a discriminator-wrapped location or bounds value to plain GeoJSON.

    Looks up the appropriate converter from ``converter_map`` using the
    payload's ``data_type`` field, applies it, then re-wraps the result in the
    discriminator format expected by the reconstructed Pydantic model.

    Args:
        payload (dict): Discriminator-wrapped value with ``data_type`` and
            ``value`` keys.
        request_type (Literal["location", "bounds"]): Whether to use the
            location or bounds converter map.

    Returns:
        dict: A new discriminator-wrapped dict with ``data_type="geojson"``
        and the converted ``value``.
    """
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


def inject_db(func_kwargs: dict, db_param_name: str | None) -> None:
    """Inject a ``LyraDBImplicit`` instance into *func_kwargs* for the db parameter.

    Does nothing if *db_param_name* is ``None``.

    Args:
        func_kwargs (dict): The kwargs dict passed to the task function.
            Modified in-place.
        db_param_name (str | None): Name of the parameter that expects a
            ``LyraDB`` instance, or ``None`` if the function has no db
            parameter.
    """
    if db_param_name is None:
        return
    from lyra_app.db.client import LyraDBImplicit  # noqa: PLC0415

    func_kwargs[db_param_name] = LyraDBImplicit()


def rebuild_function_kwargs(reconstructed_model: BaseModel) -> dict:
    """Extract a plain kwargs dict from a reconstructed Pydantic model.

    For fields that are discriminator-union wrappers (i.e. have both
    ``data_type`` and ``value`` attributes), only the inner ``value`` is kept;
    all other fields are passed through as-is.

    Args:
        reconstructed_model (BaseModel): A validated Pydantic model instance.

    Returns:
        dict: A ``{field_name: value}`` dict suitable for ``**`` unpacking
        into the plugin's calculation function.
    """
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
    """Apply explicit-type conversions to the matching parameters in *validated_dict*.

    For each parameter listed in *conversion_map*, determines the request type
    (``"location"`` or ``"bounds"``) from the associated tags and delegates to
    `convert_explicit_type`. *validated_dict* is modified in-place.

    Args:
        validated_dict (dict): The deserialised task request payload. Modified
            in-place.
        conversion_map (dict[str, list[str]]): Maps parameter names to their
            conversion tags (``REQUIRE_EXPLICIT_TYPE`` or
            ``REQUIRE_EXPLICIT_BOUNDS_TYPE``).
    """
    for param_name, tags in conversion_map.items():
        if "REQUIRE_EXPLICIT_TYPE" in tags:
            request_type = "location"
        elif "REQUIRE_EXPLICIT_BOUNDS_TYPE" in tags:
            request_type = "bounds"

        validated_dict[param_name] = convert_explicit_type(
            validated_dict[param_name],
            request_type=request_type,
        )


def _resolve_cache(
    task_id: str,
    task_name: str,
    validated_dict: dict,
) -> tuple[str | None, bool]:
    """Check the deterministic cache for a met_zone_code task request.

    Only requests containing a ``data_type="met_zone_code"`` parameter are
    eligible for caching. On a cache hit the cached payload is written to
    ``result_data_{task_id}`` in Redis.

    Args:
        task_id (str): The Celery task ID for this request.
        task_name (str): The registered task name, used to build the cache key.
        validated_dict (dict): The deserialised task request payload.

    Returns:
        tuple[str | None, bool]: A ``(det_key, cache_hit)`` pair where
        *det_key* is the deterministic Redis cache key (or ``None`` if the
        request is not cacheable) and *cache_hit* is ``True`` if a cached
        result was found and stored.
    """
    if not _has_met_zone_code(validated_dict):
        return None, False
    det_key = _build_deterministic_cache_key(task_name, validated_dict)
    cached = redis_client_sync.get(det_key)
    if cached is not None:
        cached_bytes = cast("bytes", cached)
        redis_client_sync.setex(f"result_data_{task_id}", 600, cached_bytes)
        logger.info(
            "Celery task %s serving cached result for %s (key: %s)",
            task_id,
            task_name,
            det_key,
        )
        return det_key, True
    return det_key, False


def _store_result(task_id: str, result: dict, det_key: str | None) -> dict:
    """Serialise a task result and persist it to Redis.

    Always writes to ``result_data_{task_id}`` with a 10-minute TTL. If
    *det_key* is provided, the result is also cached under the deterministic
    key with a 24-hour TTL.

    Args:
        task_id (str): The Celery task ID.
        result (dict): The raw result returned by the calculation function.
        det_key (str | None): Deterministic cache key to write, or ``None``
            to skip deterministic caching.

    Returns:
        dict: A ``{"status": "success", "download_id": task_id}`` notification
        dict ready to be published to the task's Redis pub/sub channel.
    """
    full_payload = {"status": "success", "result": result}
    serialised_payload = json.dumps(full_payload)
    redis_client_sync.setex(f"result_data_{task_id}", 600, serialised_payload)
    if det_key is not None:
        redis_client_sync.setex(det_key, 86400, serialised_payload)
    return {"status": "success", "download_id": task_id}


def _handle_task_exception(task_id: str, metric_name: str, exc: Exception) -> dict:
    """Log a task failure and build an error notification dict.

    Args:
        task_id (str): The Celery task ID.
        metric_name (str): The registered task name, used in the log message.
        exc (Exception): The exception that caused the failure.

    Returns:
        dict: A ``{"status": "error", "error_type": "worker", "message": ...}``
        notification dict ready to be published to the task's Redis pub/sub
        channel.
    """
    logger.exception(
        "Celery task %s failed while executing metric %s",
        task_id,
        metric_name,
    )
    return {"status": "error", "error_type": "worker", "message": str(exc)}


def _publish_notification(task_id: str, notification: dict) -> dict:
    """Publish a notification dict to the task's Redis pub/sub channel.

    Args:
        task_id (str): The Celery task ID. Used to derive the channel name
            ``task_results_{task_id}``.
        notification (dict): The notification payload to serialise and publish.

    Returns:
        dict: The same *notification* dict, passed through unchanged.
    """
    channel_name = f"task_results_{task_id}"
    redis_client_sync.publish(channel_name, json.dumps(notification))
    return notification


def _build_func_kwargs(
    validated_dict: dict,
    conversion_map: dict[str, list[str]],
    ModelClass: type[BaseModel],  # noqa: N803
    db_param_name: str | None,
) -> dict:
    """Deserialise a task payload into a ready-to-call kwargs dict.

    Applies explicit-type conversions, reconstructs the Pydantic model,
    unwraps any discriminator-union wrappers, and injects the ``LyraDB``
    instance if required.

    Args:
        validated_dict (dict): The deserialised task request payload.
        conversion_map (dict[str, list[str]]): Maps parameter names to their
            conversion tags.
        ModelClass (type[BaseModel]): The Pydantic model class used to
            validate and reconstruct the payload.
        db_param_name (str | None): Name of the ``LyraDB`` parameter to
            inject, or ``None`` if not required.

    Returns:
        dict: A ``{param_name: value}`` dict ready to be unpacked into the
        plugin's calculation function.
    """
    update_validated_dict_with_converted_types(validated_dict, conversion_map)
    reconstructed_model = ModelClass(**validated_dict)
    func_kwargs = rebuild_function_kwargs(reconstructed_model)
    inject_db(func_kwargs, db_param_name)
    return func_kwargs


def _publish_cache_hit(task_id: str) -> dict:
    """Publish a cache-hit success notification and return it.

    Args:
        task_id (str): The Celery task ID.

    Returns:
        dict: The published ``{"status": "success", "download_id": task_id}``
        notification dict.
    """
    notification = {"status": "success", "download_id": task_id}
    return _publish_notification(task_id, notification)


def make_celery_wrapper(
    original_calculate_func: FunctionType,
    ModelClass: type[BaseModel],  # noqa: N803
    conversion_map: dict[str, list[str]],
    db_param_name: str | None,
) -> Callable:
    """Create a Celery task wrapper for a single-step ``calculate`` function.

    Checks the deterministic cache before running. On a miss, deserialises the
    payload, calls *original_calculate_func*, stores the result in Redis, and
    publishes a notification to the task's pub/sub channel.

    Args:
        original_calculate_func (FunctionType): The plugin's ``calculate``
            function.
        ModelClass (type[BaseModel]): The Pydantic model used to validate and
            reconstruct the request payload.
        conversion_map (dict[str, list[str]]): Maps parameter names to their
            explicit-type conversion tags.
        db_param_name (str | None): Name of the ``LyraDB`` parameter to
            inject, or ``None``.

    Returns:
        Callable: A Celery-compatible bound-task wrapper function.
    """

    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id
        task_name = str(self.name or original_calculate_func.__name__)
        det_key, cache_hit = _resolve_cache(task_id, task_name, validated_dict)
        if cache_hit:
            return _publish_cache_hit(task_id)

        try:
            func_kwargs = _build_func_kwargs(
                validated_dict, conversion_map, ModelClass, db_param_name
            )
            result = original_calculate_func(**func_kwargs)
            notification = _store_result(task_id, result, det_key)
        except Exception as e:
            notification = _handle_task_exception(
                task_id,
                getattr(self, "name", original_calculate_func.__module__),
                e,
            )

        return _publish_notification(task_id, notification)

    wrapper.__name__ = original_calculate_func.__name__
    return wrapper


def make_celery_wrapper_file(
    original_calculate_func: FunctionType,
    ModelClass: type[BaseModel],  # noqa: N803
    conversion_map: dict[str, list[str]],
    db_param_name: str | None,
) -> Callable:
    """Create a Celery task wrapper for a file-returning ``calculate`` function.

    Like `make_celery_wrapper` but expects the plugin function to return a
    file path. The result is stored in Redis as a ``result_type="file"``
    payload. Deterministic caching is not applied for file-returning tasks.

    Args:
        original_calculate_func (FunctionType): The plugin's file-returning
            ``calculate`` function.
        ModelClass (type[BaseModel]): The Pydantic model used to validate and
            reconstruct the request payload.
        conversion_map (dict[str, list[str]]): Maps parameter names to their
            explicit-type conversion tags.
        db_param_name (str | None): Name of the ``LyraDB`` parameter to
            inject, or ``None``.

    Returns:
        Callable: A Celery-compatible bound-task wrapper function.
    """

    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id

        try:
            func_kwargs = _build_func_kwargs(
                validated_dict, conversion_map, ModelClass, db_param_name
            )
            file_path = original_calculate_func(**func_kwargs)
            full_payload = {
                "status": "success",
                "result_type": "file",
                "file_path": str(file_path),
            }
            redis_client_sync.setex(
                f"result_data_{task_id}", 600, json.dumps(full_payload)
            )
            notification = {"status": "success", "download_id": task_id}
        except Exception as e:
            notification = _handle_task_exception(
                task_id,
                getattr(self, "name", original_calculate_func.__module__),
                e,
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
    db_param_name: str | None,
) -> Callable:
    """Create a Celery task wrapper for a three-function batched processor.

    Checks the deterministic cache before running. On a miss, calls
    *prepare_func*, then *for_items_func* once per item in the items dict,
    and finally *aggregate_func* to combine the results. The final result is
    stored in Redis and a notification is published.

    Args:
        prepare_func (FunctionType): The plugin's ``calculate_prepare``
            function.
        for_items_func (FunctionType): The plugin's ``calculate_for_items``
            function, called once per item.
        aggregate_func (FunctionType): The plugin's ``calculate_aggregate``
            function.
        ModelClass (type[BaseModel]): The Pydantic model used to validate and
            reconstruct the request payload.
        conversion_map (dict[str, list[str]]): Maps parameter names to their
            explicit-type conversion tags.
        items_default (dict): Fallback items dict used when none is supplied
            in the request.
        db_param_name (str | None): Name of the ``LyraDB`` parameter to
            inject, or ``None``.

    Returns:
        Callable: A Celery-compatible bound-task wrapper function.
    """

    def wrapper(self: Task, validated_dict: dict) -> dict[str, str]:
        task_id = self.request.id
        task_name = str(self.name or prepare_func.__name__)
        det_key, cache_hit = _resolve_cache(task_id, task_name, validated_dict)
        if cache_hit:
            return _publish_cache_hit(task_id)

        try:
            func_kwargs = _build_func_kwargs(
                validated_dict, conversion_map, ModelClass, db_param_name
            )

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
                task_id,
                getattr(self, "name", prepare_func.__module__),
                e,
            )

        return _publish_notification(task_id, notification)

    wrapper.__name__ = prepare_func.__name__
    return wrapper


def register_tasks() -> None:
    """Register all tasks from ``TASK_REGISTRY`` with the Celery application.

    Iterates over every entry in ``TASK_REGISTRY`` and wraps each plugin
    function with the appropriate Celery wrapper (standard, file-returning, or
    batched), then registers it with `celery_app` under the metric's name.
    """
    for metric_name, info in TASK_REGISTRY.items():
        db_param_name = info.get("db_param_name")
        if info["is_batched"]:
            wrapped_function = make_celery_wrapper_batched(
                info["calculate_prepare"],
                info["calculate_for_items"],
                info["calculate_aggregate"],
                info["model"],
                info["params_to_convert"],
                info["items_default"],
                db_param_name,
            )
        elif info["returns_file"]:
            wrapped_function = make_celery_wrapper_file(
                info["calculate"],
                info["model"],
                info["params_to_convert"],
                db_param_name,
            )
        else:
            wrapped_function = make_celery_wrapper(
                info["calculate"],
                info["model"],
                info["params_to_convert"],
                db_param_name,
            )
        celery_app.task(name=metric_name, bind=True)(wrapped_function)


register_tasks()


_INTERRUPTED_TASK_MESSAGE = (
    "This task was interrupted because plugins were updated. Please retry."
)


def notify_interrupted_tasks(task_ids: list[str]) -> None:
    """Publish an error notification to each task's Redis pub/sub channel.

    Called before forcibly terminating workers so that connected websocket
    clients receive an error response instead of waiting indefinitely.

    Args:
        task_ids (list[str]): IDs of the Celery tasks to notify.
    """
    for task_id in task_ids:
        payload = json.dumps(
            {
                "status": "error",
                "error_type": "worker",
                "message": _INTERRUPTED_TASK_MESSAGE,
            }
        )
        redis_client_sync.publish(f"task_results_{task_id}", payload)
        logger.info("Notified task %s of interruption.", task_id)


def graceful_worker_restart(timeout: float = 30.0) -> None:
    """Drain active Celery tasks then broadcast a shutdown to all workers.

    Polls `inspect().active()` every second up to *timeout* seconds. If all
    workers become idle within the window, a graceful shutdown is issued
    immediately. If the timeout is exceeded, any remaining in-flight tasks are
    notified via their Redis pub/sub channels and then revoked and terminated
    before the shutdown is sent. Docker's `restart: unless-stopped` policy
    brings the workers back up automatically.

    Args:
        timeout (float): Maximum seconds to wait for in-flight tasks to drain
            before force-terminating them. Defaults to ``30.0``.
    """
    inspector = celery_app.control.inspect()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        active = inspector.active()
        if not active or all(len(tasks) == 0 for tasks in active.values()):
            logger.info("All workers idle; issuing graceful shutdown.")
            celery_app.control.broadcast("shutdown")
            return
        time.sleep(1)

    # Timeout exceeded — collect and terminate remaining in-flight tasks.
    active = inspector.active() or {}
    interrupted_ids: list[str] = [
        task["id"] for tasks in active.values() for task in tasks
    ]

    if interrupted_ids:
        logger.warning(
            "Timeout exceeded with %d task(s) still running; terminating.",
            len(interrupted_ids),
        )
        notify_interrupted_tasks(interrupted_ids)
        for task_id in interrupted_ids:
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    celery_app.control.broadcast("shutdown")
