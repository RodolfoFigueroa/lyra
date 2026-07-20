from __future__ import annotations

from typing import Annotated

from lyra.sdk import LocationInput, PluginDefinition, RunContext
from lyra.sdk.models import FileJobResult, TableJobResult
from lyra.sdk.models.plugin_v3 import (
    FileOutputV3,
    TableOutputColumnV3,
    TableOutputV3,
)
from pydantic import Field

plugin = PluginDefinition()


def _feature_ids(location: LocationInput) -> list[str]:
    return [feature.id for feature in location.features]


def _value_output() -> TableOutputV3:
    return TableOutputV3(
        kind="table",
        columns=[
            TableOutputColumnV3(
                name="value",
                type="integer",
                unit="count",
                description="Submitted value.",
            )
        ],
    )


# docs:start table-metric
@plugin.metric(
    name="smoke_table_metric",
    description="Return the submitted value for each input feature.",
    output=_value_output(),
)
def run_table(
    location: LocationInput,
    value: Annotated[int, Field(description="Value copied into each output row.")],
    *,
    context: RunContext,
) -> TableJobResult:
    context.emit_event("progress", {"stage": "table"})
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=feature_ids,
        columns=["value"],
        values={"value": [value for _feature_id in feature_ids]},
    )


# docs:end table-metric


@plugin.metric(
    name="smoke_file_metric",
    description="Write a small text artifact for the submitted features.",
    output=FileOutputV3(
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
    context.emit_event("progress", {"stage": "file"})
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


@plugin.metric(
    name="smoke_cancel_metric",
    description="Emit progress and observe cancellation before returning.",
    output=_value_output(),
)
def run_cancel(
    location: LocationInput,
    value: Annotated[int, Field(description="Value copied into each output row.")],
    *,
    context: RunContext,
) -> TableJobResult:
    context.emit_event("progress", {"stage": "cancel-check"})
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return TableJobResult.from_mapping(
        job_id=context.job_id,
        input_index=feature_ids,
        columns=["value"],
        values={"value": [value for _feature_id in feature_ids]},
    )
