from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import pytest
from lyra.sdk import (
    Bounds,
    DatabaseNotConfiguredError,
    LocalRunContext,
    LocationInput,
    LyraDB,
    PluginDefinition,
    RunCancelledError,
    RunContext,
    StubLyraDB,
    metric,
)
from lyra.sdk.models import (
    JobEnvelope,
    JobMessageEvent,
    JobProgressEvent,
    TableJobResult,
)
from lyra.sdk.models.plugin_v4 import TableOutputColumnV4, TableOutputV4
from pydantic import ValidationError
from typing_extensions import Unpack, override

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import geopandas


class FakeLyraDB(LyraDB):
    def __init__(self) -> None:
        self.mesh_calls: list[tuple[Bounds, int]] = []

    @override
    def load_denue_from_bounds(
        self,
        bounds: Bounds,
        *,
        year: Literal[2020, 2021, 2022, 2023, 2024, 2025],
        month: Literal[5, 11],
    ) -> geopandas.GeoDataFrame:
        raise AssertionError((bounds, year, month))

    @override
    def load_mesh_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal[4, 5, 6, 7, 8, 9] = 9,
    ) -> geopandas.GeoDataFrame:
        self.mesh_calls.append((bounds, level))
        return cast("geopandas.GeoDataFrame", {"kind": "fake-mesh"})

    @override
    def load_census_from_bounds(
        self,
        bounds: Bounds,
        *,
        level: Literal["ent", "mun", "loc", "ageb", "mza"],
        columns: Sequence[str],
    ) -> geopandas.GeoDataFrame:
        raise AssertionError((bounds, level, columns))


class _ContextOptions(TypedDict, total=False):
    job_id: str
    metric: str
    temp_dir: Path
    db: LyraDB
    logger: logging.Logger


def _context(tmp_path: Path, **overrides: Unpack[_ContextOptions]) -> LocalRunContext:
    return LocalRunContext(
        job_id=overrides.get("job_id", "local-job"),
        metric=overrides.get("metric", "example"),
        temp_dir=overrides.get("temp_dir", tmp_path / "outputs"),
        db=overrides.get("db"),
        logger=overrides.get("logger"),
    )


def _accepts_run_context(context: RunContext) -> RunContext:
    return context


def _feature_collection() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "id": "result",
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [-99.1, 19.4],
                },
                "properties": {},
            }
        ],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def test_constructs_with_minimum_arguments_and_satisfies_protocol(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    compatible = _accepts_run_context(context)

    assert compatible is context
    assert context.job_id == "local-job"
    assert context.metric == "example"
    assert context.temp_dir == tmp_path / "outputs"
    assert isinstance(context.db, StubLyraDB)
    assert context.events == ()
    assert context.cancelled is False


@pytest.mark.parametrize("value", ["", " \t"])
def test_rejects_blank_job_id(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="job_id must be a non-empty string"):
        _context(tmp_path, job_id=value)


@pytest.mark.parametrize("value", ["", " \t"])
def test_rejects_blank_metric(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="metric must be a non-empty string"):
        _context(tmp_path, metric=value)


def test_creates_and_preserves_caller_owned_directory(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "plugin-output"
    context = _context(tmp_path, temp_dir=output)
    artifact = context.temp_dir / "result.txt"
    artifact.write_text("inspectable")

    del context

    assert output.is_dir()
    assert artifact.read_text() == "inspectable"


def test_rejects_temp_dir_that_is_a_file(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.write_text("not a directory")

    with pytest.raises(NotADirectoryError, match="temp_dir is not a directory"):
        _context(tmp_path, temp_dir=output)


def test_uses_stable_default_logger_and_allows_injection(tmp_path: Path) -> None:
    default_context = _context(tmp_path)
    explicit = logging.getLogger("plugin.test")

    assert default_context.logger is logging.getLogger("lyra.sdk.local")
    assert _context(tmp_path, logger=explicit).logger is explicit


def test_each_context_gets_a_fresh_strict_database_stub(tmp_path: Path) -> None:
    first = _context(tmp_path)
    second = _context(tmp_path, temp_dir=tmp_path / "second")

    assert isinstance(first.db, StubLyraDB)
    assert isinstance(second.db, StubLyraDB)
    assert first.db is not second.db


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (
            "load_denue_from_bounds",
            {"bounds": Bounds(0, 0, 1, 1), "year": 2025, "month": 11},
        ),
        (
            "load_mesh_from_bounds",
            {"bounds": Bounds(0, 0, 1, 1), "level": 9},
        ),
        (
            "load_census_from_bounds",
            {"bounds": Bounds(0, 0, 1, 1), "level": "mza", "columns": ["pobtot"]},
        ),
    ],
)
def test_default_stub_rejects_every_database_operation_without_arguments(
    tmp_path: Path,
    operation: str,
    arguments: dict[str, Any],
) -> None:
    context = _context(tmp_path)

    with pytest.raises(DatabaseNotConfiguredError) as error:
        getattr(context.db, operation)(**arguments)

    assert error.value.operation == operation
    assert str(error.value).count(operation) == 1
    assert "pobtot" not in str(error.value)
    assert repr(Bounds(0, 0, 1, 1)) not in str(error.value)


def test_uses_injected_database_implementation(tmp_path: Path) -> None:
    database = FakeLyraDB()
    context = _context(tmp_path, db=database)
    bounds = Bounds(0, 0, 1, 1)

    result = context.db.load_mesh_from_bounds(bounds, level=8)

    assert result == {"kind": "fake-mesh"}
    assert database.mesh_calls == [(bounds, 8)]


def test_captures_mixed_events_chronologically_with_utc_timestamps(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    context.report_progress(stage="load", current=0, total=2, unit="files")
    context.report_message("Loaded input", fields={"count": 2})
    context.report_progress(stage="load", current=2, total=2, unit="files")

    assert [event.kind for event in context.events] == [
        "progress",
        "message",
        "progress",
    ]
    offsets = [event.timestamp.utcoffset() for event in context.events]
    assert all(offset is not None for offset in offsets)
    assert all(offset is not None and offset.total_seconds() == 0 for offset in offsets)
    assert context.events[0].job_id == "local-job"
    assert context.events[0].metric == "example"


def test_filtered_event_properties_are_immutable_snapshots(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.report_progress(stage="load", current=0)
    events_snapshot = context.events
    progress_snapshot = context.progress_events
    message_snapshot = context.message_events

    context.report_message("Later")

    assert isinstance(events_snapshot, tuple)
    assert isinstance(progress_snapshot, tuple)
    assert isinstance(message_snapshot, tuple)
    assert len(events_snapshot) == 1
    assert progress_snapshot == (events_snapshot[0],)
    assert message_snapshot == ()
    assert len(context.events) == 2
    assert all(isinstance(event, JobProgressEvent) for event in context.progress_events)
    assert all(isinstance(event, JobMessageEvent) for event in context.message_events)


def test_event_models_enforce_production_validation(tmp_path: Path) -> None:
    context = _context(tmp_path)

    with pytest.raises(ValidationError):
        context.report_progress(stage="", current=0)
    with pytest.raises(ValidationError):
        context.report_progress(stage="load", current=-1)
    with pytest.raises(ValidationError):
        context.report_message("")
    with pytest.raises(ValidationError):
        context.report_message("Invalid field", fields={"value": object()})

    assert context.events == ()


def test_rejected_progress_transition_does_not_change_state(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.report_progress(stage="load", current=1, total=3, unit="files")

    with pytest.raises(ValueError, match="must not decrease"):
        context.report_progress(stage="load", current=0, total=3, unit="files")

    context.report_progress(stage="load", current=2, total=3, unit="files")
    assert [event.current for event in context.progress_events] == [1, 2]


def test_cancellation_is_cooperative_and_does_not_mutate_events(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.report_message("Before cancellation")
    context.check_cancelled()

    context.cancel()

    assert context.cancelled is True
    assert len(context.events) == 1
    for _attempt in range(2):
        with pytest.raises(RunCancelledError, match="local-job"):
            context.check_cancelled()
    assert len(context.events) == 1


def test_plugin_definition_executes_directly_with_local_context(tmp_path: Path) -> None:
    output = TableOutputV4(
        kind="table",
        columns=[
            TableOutputColumnV4(
                name="value",
                type="integer",
                unit="count",
                description="Locally computed value.",
            )
        ],
    )

    @metric(
        name="local_example",
        description="Exercise SDK-only local execution.",
        output=output,
    )
    def calculate(location: LocationInput, *, context: RunContext) -> TableJobResult:
        del location
        context.check_cancelled()
        context.logger.info("Running local example")
        (context.temp_dir / "details.txt").write_text("kept")
        context.report_progress(stage="compute", current=1, total=1, unit="item")
        context.report_message("Local run complete")
        return TableJobResult(
            job_id=context.job_id,
            index=["result"],
            columns=["value"],
            data=[[1]],
        )

    plugin = PluginDefinition(metrics=[calculate])
    context = _context(tmp_path, metric="local_example")

    result = plugin(
        JobEnvelope(
            job_id="local-job",
            metric="local_example",
            input={"location": _feature_collection()},
        ),
        context,
    )

    assert isinstance(result, TableJobResult)
    assert result.data == [[1]]
    assert (context.temp_dir / "details.txt").read_text() == "kept"
    assert [event.kind for event in context.events] == ["progress", "message"]
