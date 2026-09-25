from pathlib import Path
from typing import Self

import pandas as pd
from lyra.sdk import (
    BoundsInput,
    FileOutput,
    LocationInput,
    MetricParameters,
    RunContext,
    TableColumn,
    TableOutput,
    metric,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .calculation import calculate_capacity


class CapacityParameters(MetricParameters):
    units: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Capacity assigned to each selected feature.",
        examples=[5, 10],
    )


CAPACITY_OUTPUT = TableOutput(
    columns=[
        TableColumn(
            name="capacity",
            type="integer",
            unit="units",
            description="Capacity of the selected feature.",
        )
    ]
)


@metric(
    name="capacity",
    description="Assign a capacity to each selected feature.",
    output=CAPACITY_OUTPUT,
)
def capacity(parameters: CapacityParameters, location: LocationInput) -> pd.DataFrame:
    return calculate_capacity(
        [feature.id for feature in location.features],
        units_per_feature=parameters.units,
    )


class ExistingSettings(BaseModel):
    units: int = Field(default=4, ge=1, description="Units per selected feature.")


class CompatibleSettings(ExistingSettings):
    model_config = ConfigDict(extra="forbid", validate_default=True)


@metric(
    name="reused_capacity",
    description="Use parameters adapted from an existing library.",
    output=CAPACITY_OUTPUT,
)
def reused_capacity(
    parameters: CompatibleSettings, location: LocationInput
) -> pd.DataFrame:
    return calculate_capacity(
        [feature.id for feature in location.features],
        units_per_feature=parameters.units,
    )


class Selection(MetricParameters):
    activity_codes: list[str] = Field(
        min_length=1, description="Activity codes included in the selection."
    )


class SelectionParameters(MetricParameters):
    selection: Selection = Field(description="Activity selection settings.")
    threshold: int | None = Field(
        ge=0, description="Required threshold; null disables the threshold."
    )
    label: str | None = Field(default=None, description="Optional descriptive label.")


@metric(
    name="selection_size",
    description="Count selected activity codes above an optional threshold.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="selected_count",
                type="integer",
                unit="codes",
                description="Number of selected codes passing the threshold.",
            )
        ]
    ),
)
def selection_size(
    parameters: SelectionParameters, location: LocationInput
) -> pd.DataFrame:
    count = len(parameters.selection.activity_codes)
    if parameters.threshold is not None and count < parameters.threshold:
        count = 0
    return pd.DataFrame(
        {"selected_count": [count] * len(location.features)},
        index=[feature.id for feature in location.features],
    )


@metric(
    name="bounded_features",
    description="Describe the supplied bounds for each selected location.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="bounds_type",
                type="string",
                unit="geometry_type",
                description="Geometry type of the supplied bounding feature.",
            )
        ]
    ),
)
def bounded_features(location: LocationInput, bounds: BoundsInput) -> pd.DataFrame:
    return pd.DataFrame(
        {"bounds_type": [bounds.features[0].geometry.type] * len(location.features)},
        index=[feature.id for feature in location.features],
    )


@metric(
    name="feature_report",
    description="Write the selected feature identifiers to a text report.",
    output=FileOutput(media_type="text/plain", extensions=[".txt"]),
)
def feature_report(location: LocationInput, context: RunContext) -> Path:
    context.check_cancelled()
    context.logger.info("Writing %d feature identifiers", len(location.features))
    destination = context.temp_dir / "features.txt"
    destination.write_text(
        "".join(f"{feature.id}\n" for feature in location.features), encoding="utf-8"
    )
    return destination


class IntervalParameters(MetricParameters):
    lower: int = Field(ge=0, description="Lower interval boundary.")
    upper: int = Field(ge=0, description="Upper interval boundary.")

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            message = "lower must not exceed upper"
            raise ValueError(message)
        return self


@metric(
    name="interval_width",
    description="Return the width of a valid interval for each location.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="width",
                type="integer",
                unit="units",
                description="Difference between the upper and lower boundary.",
            )
        ]
    ),
)
def interval_width(
    parameters: IntervalParameters, location: LocationInput
) -> pd.DataFrame:
    return pd.DataFrame(
        {"width": [parameters.upper - parameters.lower] * len(location.features)},
        index=[feature.id for feature in location.features],
    )
