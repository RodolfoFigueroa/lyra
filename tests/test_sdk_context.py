"""Tests for shared SDK run-context semantics."""

from datetime import UTC, datetime

import pytest
from lyra.sdk import RunCancelledError
from lyra.sdk.models import JobProgressEvent
from lyra.sdk.run_context_state import RunProgressState

from lyra_app import job_store


def _progress_event(
    *,
    stage: str = "compute",
    current: float = 0,
    total: float | None = None,
    unit: str | None = None,
) -> JobProgressEvent:
    return JobProgressEvent(
        job_id="job-1",
        metric="metric",
        timestamp=datetime.now(UTC),
        stage=stage,
        current=current,
        total=total,
        unit=unit,
    )


def test_run_progress_state_accepts_first_event() -> None:
    state = RunProgressState()
    event = _progress_event(current=1)

    state.accept(event)

    assert state.last_event is event


def test_run_progress_state_accepts_monotonic_progress_within_stage() -> None:
    state = RunProgressState()
    state.accept(_progress_event(current=1, total=3, unit="tiles"))
    event = _progress_event(current=2, total=3, unit="tiles")

    state.accept(event)

    assert state.last_event is event


def test_run_progress_state_allows_unknown_total_to_become_stable() -> None:
    state = RunProgressState()
    state.accept(_progress_event(current=1))
    concrete = _progress_event(current=2, total=4)
    state.accept(concrete)

    state.accept(_progress_event(current=3, total=4))

    assert state.last_event is not None
    assert state.last_event.total == 4


def test_run_progress_state_resets_transition_baseline_for_new_stage() -> None:
    state = RunProgressState()
    state.accept(_progress_event(current=4, total=4, unit="tiles"))
    event = _progress_event(stage="save", current=0, unit="files")

    state.accept(event)

    assert state.last_event is event


def test_run_progress_state_rejects_decreasing_current() -> None:
    state = RunProgressState()
    accepted = _progress_event(current=2)
    state.accept(accepted)

    with pytest.raises(ValueError, match="must not decrease"):
        state.accept(_progress_event(current=1))

    assert state.last_event is accepted


@pytest.mark.parametrize("total", [None, 4])
def test_run_progress_state_rejects_changing_or_removing_concrete_total(
    total: float | None,
) -> None:
    state = RunProgressState()
    accepted = _progress_event(current=1, total=3)
    state.accept(accepted)

    with pytest.raises(ValueError, match=r"total.*must remain stable"):
        state.accept(_progress_event(current=2, total=total))

    assert state.last_event is accepted


@pytest.mark.parametrize(
    ("initial_unit", "later_unit"),
    [(None, "tiles"), ("tiles", None), ("tiles", "features")],
)
def test_run_progress_state_rejects_changing_unit(
    initial_unit: str | None,
    later_unit: str | None,
) -> None:
    state = RunProgressState()
    accepted = _progress_event(unit=initial_unit)
    state.accept(accepted)

    with pytest.raises(ValueError, match=r"unit.*must remain stable"):
        state.accept(_progress_event(current=1, unit=later_unit))

    assert state.last_event is accepted


def test_rejected_progress_does_not_poison_later_valid_event() -> None:
    state = RunProgressState()
    state.accept(_progress_event(current=1, total=3))

    with pytest.raises(ValueError, match="must not decrease"):
        state.accept(_progress_event(current=0, total=3))

    valid = _progress_event(current=2, total=3)
    state.accept(valid)
    assert state.last_event is valid


def test_job_cancelled_error_is_catchable_as_run_cancelled_error() -> None:
    job_id = "job-1"
    with pytest.raises(RunCancelledError):
        raise job_store.JobCancelledError(job_id)
