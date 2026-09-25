"""Metric functions used by the example smoke-test plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from lyra.sdk import LocationInput, MetricParameters, RunContext, metric
from lyra.sdk.models.plugin import (
    FileOutput,
    TableColumn,
    TableOutput,
)
from pydantic import Field

if TYPE_CHECKING:
    from pathlib import Path


class Parameters(MetricParameters):
    """Parameters shared by the table and cancellation examples."""

    value: int = Field(description="Value copied into each output row.")


def _feature_ids(location: LocationInput) -> list[str]:
    return [feature.id for feature in location.features]


def _value_output() -> TableOutput:
    return TableOutput(
        kind="table",
        columns=[
            TableColumn(
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
    output=_value_output(),
)
def run_table(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Copy the submitted integer into a row for every input feature.

    Returns:
        A table result indexed by the input feature identifiers.
    """
    context.report_progress(stage="table", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return pd.DataFrame(
        {"value": [parameters.value for _feature_id in feature_ids]},
        index=feature_ids,
    )


# docs:end table-metric


@metric(
    name="smoke_file_metric",
    description="Write a small text artifact for the submitted features.",
    output=FileOutput(
        kind="file",
        media_type="text/plain",
        extensions=[".txt"],
    ),
)
def run_file(
    location: LocationInput,
    *,
    context: RunContext,
) -> Path:
    """Write the input feature identifiers to a small text artifact.

    Returns:
        A file result referring to the generated text artifact.
    """
    context.report_progress(stage="file", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    output_path = context.temp_dir / "smoke-result.txt"
    output_path.write_text(
        "\n".join(["smoke file result", *feature_ids]) + "\n",
        encoding="utf-8",
    )
    return output_path


@metric(
    name="smoke_cancel_metric",
    description="Emit progress and observe cancellation before returning.",
    output=_value_output(),
)
def run_cancel(
    location: LocationInput,
    parameters: Parameters,
    *,
    context: RunContext,
) -> pd.DataFrame:
    """Exercise cancellation reporting before producing a table result.

    Returns:
        A table result when the context has not been cancelled.
    """
    context.report_progress(stage="cancel-check", current=1, total=1)
    context.check_cancelled()
    feature_ids = _feature_ids(location)
    return pd.DataFrame(
        {"value": [parameters.value for _feature_id in feature_ids]},
        index=feature_ids,
    )
