from __future__ import annotations

import json
from typing import Protocol


class JobScriptClient(Protocol):
    values: dict[str, str]

    def set(self, key: str, value: str, *, ex: int) -> object: ...

    def expire(self, key: str, ttl: int) -> object: ...

    def xadd(self, key: str, fields: dict[str, str]) -> str: ...

    def zadd(self, key: str, mapping: dict[str, float]) -> object: ...

    def zremrangebyscore(
        self,
        key: str,
        minimum: str | float,
        maximum: float,
        /,
    ) -> object: ...


def _transition_error(current: str | None, guard: str) -> str | None:
    if guard == "missing":
        return "error:exists" if current is not None else None
    if current is None:
        return "error:missing"
    current_status = json.loads(current)["status"]
    if current_status in {"succeeded", "failed", "cancelled"}:
        return f"error:terminal:{current_status}"
    if guard in {"queued", "running"} and current_status != guard:
        return f"error:expected-{guard}:{current_status}"
    return None


def _eval_event_script(
    client: JobScriptClient,
    keys: list[str],
    args: tuple[str | float, ...],
) -> str:
    error = _transition_error(client.values.get(keys[0]), str(args[0]))
    if error is not None:
        return error
    status_payload, event_kind, event_payload = map(str, args[1:4])
    ttl = int(args[4])
    score = float(args[5])
    job_id = str(args[6])
    cutoff = float(args[7])
    client.set(keys[0], status_payload, ex=ttl)
    stream_id = client.xadd(keys[1], {"event": event_kind, "payload": event_payload})
    for key in keys[1:4]:
        client.expire(key, ttl)
    reservation_key = client.values.get(keys[3])
    if reservation_key is not None:
        client.expire(reservation_key, ttl)
    client.zadd(keys[4], {job_id: score})
    client.zremrangebyscore(keys[4], "-inf", cutoff)
    return stream_id


def _eval_terminal_script(
    client: JobScriptClient,
    keys: list[str],
    args: tuple[str | float, ...],
) -> int:
    current = client.values.get(keys[0])
    if current is None:
        return 0
    if json.loads(current)["status"] in {"succeeded", "failed", "cancelled"}:
        return 0
    result_payload, status_payload, event_kind, event_payload = map(str, args[:4])
    ttl = int(args[4])
    score = float(args[5])
    job_id = str(args[6])
    cutoff = float(args[7])
    client.set(keys[1], result_payload, ex=ttl)
    client.set(keys[0], status_payload, ex=ttl)
    client.xadd(keys[2], {"event": event_kind, "payload": event_payload})
    for key in keys[2:5]:
        client.expire(key, ttl)
    client.zadd(keys[5], {job_id: score})
    client.zremrangebyscore(keys[5], "-inf", cutoff)
    return 1


def eval_job_script(
    client: JobScriptClient,
    numkeys: int,
    keys_and_args: tuple[str | float, ...],
) -> int | str:
    """Emulate job-store Lua scripts for focused in-memory test doubles."""

    if numkeys == 5:
        keys = [str(value) for value in keys_and_args[:numkeys]]
        return _eval_event_script(client, keys, keys_and_args[numkeys:])
    if numkeys != 6:
        msg = f"Unsupported test script key count: {numkeys}"
        raise AssertionError(msg)
    keys = [str(value) for value in keys_and_args[:numkeys]]
    return _eval_terminal_script(client, keys, keys_and_args[numkeys:])
