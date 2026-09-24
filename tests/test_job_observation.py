from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from lyra.sdk.models.job import FailedJobResult, JobStatusInfo, TableJobResult

from lyra_app import job_observation, job_store
from lyra_app.job_observation import JobObservation


def test_observation_reads_all_keys_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = JobStatusInfo(
        job_id="job-1",
        status="queued",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    result = TableJobResult(job_id="job-1", index=["r"], columns=["v"], data=[[1]])
    pipeline = Mock()
    pipeline.execute = AsyncMock(
        return_value=[snapshot.model_dump_json(), result.model_dump_json(), None, 1500]
    )
    context = AsyncMock()
    context.__aenter__.return_value = pipeline
    client = Mock()
    client.pipeline.return_value = context
    monkeypatch.setattr(job_observation, "redis_client", client)
    observed = asyncio.run(job_observation.read_job_observation("job-1"))
    client.pipeline.assert_called_once_with(transaction=True)
    assert [call.args[0] for call in pipeline.get.call_args_list] == [
        job_store.status_key("job-1"),
        job_store.result_key("job-1"),
        job_store.provenance_key("job-1"),
    ]
    pipeline.pttl.assert_called_once_with(job_store.result_key("job-1"))
    pipeline.execute.assert_awaited_once()
    assert observed.result == result
    assert observed.snapshot == snapshot
    assert observed.provenance is None
    assert observed.lifetime.expires_in_seconds == 2
    assert observed.lifetime.expires_at is not None


@pytest.mark.parametrize("repaired", [False, True])
def test_reconciliation_rereads_only_after_repair(
    *, repaired: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = JobStatusInfo(
        job_id="job-1",
        status="running",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    original = JobObservation(snapshot=snapshot)
    terminal = JobObservation(
        result=FailedJobResult(job_id="job-1", error={"message": "worker lost"})
    )
    read = AsyncMock(side_effect=[original, terminal])
    repair = AsyncMock(return_value=repaired)
    monkeypatch.setattr(job_observation, "read_job_observation", read)
    monkeypatch.setattr(job_observation, "repair_celery_failure", repair)
    assert asyncio.run(job_observation.observe_job("job-1")) == (
        terminal if repaired else original
    )
    assert read.await_count == (2 if repaired else 1)
    repair.assert_awaited_once_with(snapshot)


@pytest.mark.parametrize("has_result", [False, True])
def test_missing_status_and_retained_results_do_not_reconcile(
    *, has_result: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = JobObservation(
        result=FailedJobResult(job_id="job-1", error={}) if has_result else None
    )
    read = AsyncMock(return_value=original)
    repair = AsyncMock()
    monkeypatch.setattr(job_observation, "read_job_observation", read)
    monkeypatch.setattr(job_observation, "repair_celery_failure", repair)
    assert asyncio.run(job_observation.observe_job("job-1")) == original
    read.assert_awaited_once()
    repair.assert_not_called()
