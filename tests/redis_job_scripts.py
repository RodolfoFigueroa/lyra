from __future__ import annotations

import json
from typing import Any, Protocol, cast

from lyra.sdk.models.job import FailedJobResult, JobEnvelope, TableJobResult

from lyra_app import job_store


class JobScriptClient(Protocol):
    values: dict[str, str]
    sorted_sets: dict[str, dict[str, float]]
    expirations: list[tuple[str, int]]


def eval_job_script(
    client: JobScriptClient,
    numkeys: int,
    keys_and_args: tuple[str | float, ...],
    script: str,
) -> int:
    """Emulate guarded transitions for unit tests using explicit script identity."""
    keys = [str(value) for value in keys_and_args[:numkeys]]
    args = keys_and_args[numkeys:]
    raw = client.values.get(keys[0])
    if "-- create-job" in script:
        return _create(client, keys, args, raw)
    if raw is None:
        return 0
    current = json.loads(raw)
    if "-- claim-job" in script:
        if current["status"] != "queued":
            return 0
        current.update(status="running", started_at=args[0], updated_at=args[0])
    elif "-- update-progress" in script:
        if current["status"] != "running":
            return 0
        current.update(progress=json.loads(str(args[0])), updated_at=args[1])
    else:
        assert "-- complete-job" in script
        if current["status"] not in {"queued", "running"} or (
            args[3] == "queued" and current["status"] != "queued"
        ):
            return 0
        result = json.loads(str(args[0]))
        current.update(
            status=result["status"],
            error=result.get("error"),
            updated_at=args[1],
            completed_at=args[1],
        )
        client.values[keys[1]] = str(args[0])
        binding = client.values.get(keys[3])
        client.expirations.extend(
            (key, int(args[2]))
            for key in [*keys, *([binding] if binding else [])]
            if key in client.values
        )
    client.values[keys[0]] = json.dumps(current)
    return 1


def seed_status(
    job_id: str,
    status: str,
    *,
    metric: str = "heavy_metric",
    client: job_store.SyncAtomicJobWriter | None = None,
    error: dict[str, Any] | None = None,
) -> job_store.JobStatusSnapshot:
    """Prepare accepted jobs through the actual lifecycle operations in tests."""
    if status == "queued":
        return job_store.create_job(
            JobEnvelope(job_id=job_id, metric=metric, input={}), client=client
        )
    if status == "running":
        assert job_store.claim_job(job_id, client=client)
    elif status == "cancelled":
        job_store.cancel_job(job_id, client=client)
    elif status == "succeeded":
        job_store.save_job_result(
            TableJobResult(job_id=job_id, index=["a"], columns=["v"], data=[[1]]),
            client=client,
        )
    else:
        job_store.save_job_result(
            FailedJobResult(job_id=job_id, error=error or {}), client=client
        )
    snapshot = job_store.get_job_status(job_id, client=client)
    assert snapshot is not None
    return snapshot


def _create(
    client: JobScriptClient,
    keys: list[str],
    args: tuple[str | float, ...],
    raw: str | None,
) -> int:
    if raw is not None:
        return 0
    if args[3] == "reserved":
        binding = client.values.get(keys[2])
        reservation = client.values.get(binding or "")
        if not reservation or json.loads(reservation)["job_id"] != args[2]:
            return 0
        client.expirations[:] = [
            (key, ttl)
            for key, ttl in client.expirations
            if key not in {binding, keys[2]}
        ]
    client.values[keys[0]] = str(args[0])
    if args[1]:
        client.values[keys[1]] = str(args[1])
    client.sorted_sets.setdefault(keys[3], {})[str(args[2])] = float(args[4])
    return 1


async def seed_status_async(
    job_id: str,
    status: str,
    *,
    metric: str = "heavy_metric",
    client: job_store.AsyncAtomicJobWriter | None = None,
    error: dict[str, Any] | None = None,
) -> job_store.JobStatusSnapshot:
    """Seed route response fixtures with the shared lifecycle contract."""
    sync = SeedRedis()
    client = client or cast("job_store.AsyncAtomicJobWriter", job_store.redis_client)
    raw = await client.get(job_store.status_key(job_id))
    if raw is not None:
        sync.values[job_store.status_key(job_id)] = (
            raw.decode() if isinstance(raw, bytes) else raw
        )
    snapshot = seed_status(job_id, status, metric=metric, client=sync, error=error)
    # Fixtures expose their in-memory value map for route setup.
    values = cast("JobScriptClient", client).values
    previous_result = values.get(job_store.result_key(job_id))
    values.update(sync.values)
    if previous_result is not None:
        values[job_store.result_key(job_id)] = previous_result
    return snapshot


class SeedRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.expirations: list[tuple[str, int]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def eval(self, script: str, numkeys: int, key: str, /, *args: str | float) -> int:
        return eval_job_script(self, numkeys, (key, *args), script)
