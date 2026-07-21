from __future__ import annotations

from lyra.sdk import Input, LocationInput, RunContext, metric
from lyra.sdk.models import FileJobResult, TableJobResult
from lyra.sdk.models.plugin_v4 import (
    FileOutputV4,
    TableOutputColumnV4,
    TableOutputV4,
)


def _feature_ids(location: LocationInput) -> list[str]:
    return [feature.id for feature in location.features]


def _value_output() -> TableOutputV4:
    return TableOutputV4(
        kind="table",
        columns=[
            TableOutputColumnV4(
                name="value",
                type="integer",
                unit="count",
                description="Submitted value.",
            )
        ],
    )


# docs:start table-metric
@metric(
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    inputs={
        "value": Input(description="Value copied into each output row."),
    },
    output=_value_output(),
)
def run_table(
    location: LocationInput,
    value: int,
    *,
    context: RunContext,
) -> TableJobResult:
    context.report_progress(stage="table", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=feature_ids,
        columns=["value"],
        values={"value": [value for _feature_id in feature_ids]},
    )


# docs:end table-metric


@metric(
    name="smoke_file_metric",
    description="Write a small text artifact for the submitted features.",
    output=FileOutputV4(
        kind="file",
        media_type="text/plain",
        extensions=[".txt"],
    ),
)
def run_file(
    location: LocationInput,
    *,
    context: RunContext,
) -> FileJobResult:
    context.report_progress(stage="file", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    output_path = context.temp_dir / "smoke-result.txt"
    output_path.write_text(
        "\n".join(["smoke file result", *feature_ids]) + "\n",
        encoding="utf-8",
    )
    return FileJobResult(
        job_id=context.job_id,
        file_path=output_path.name,
        media_type="text/plain",
    )


@metric(
    name="smoke_cancel_metric",
    description="Emit progress and observe cancellation before returning.",
    inputs={
        "value": Input(description="Value copied into each output row."),
    },
    output=_value_output(),
)
def run_cancel(
    location: LocationInput,
    value: int,
    *,
    context: RunContext,
) -> TableJobResult:
    context.report_progress(stage="cancel-check", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=feature_ids,
        columns=["value"],
        values={"value": [value for _feature_id in feature_ids]},
    )
