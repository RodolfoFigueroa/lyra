"""Installed fixture metrics exercising the production RQ execution boundary."""

import os
import time
from typing import Literal

import pandas as pd
from lyra.sdk import (
    LocationInput,
    MetricParameters,
    PluginDefinition,
    RunContext,
    TableColumn,
    TableOutput,
    Unit,
    metric,
)
from lyra.sdk.models.plugin import FractionOfLocationArea
from lyra.utils.geometry import calculate_feature_areas_m2
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lyra_app.db.connection import get_worker_engine
from tests.fixtures.contract_plugin.metrics import (
    bounded_features,
    capacity,
    feature_report,
    interval_width,
    reused_capacity,
    selection_size,
)


class WorkParameters(MetricParameters):
    """Synthetic runtime and error controls."""

    seconds: float = Field(default=0, ge=0, description="Silent computation duration.")
    outcome: Literal["success", "crash", "invalid", "database"] = Field(
        default="success", description="Expected execution outcome."
    )
    fraction: float = Field(default=0.25, description="Fractional area result.")


@metric(
    name="work",
    description="Exercise production worker lifecycle and result validation.",
    output=TableOutput(
        columns=[
            TableColumn(
                name="area",
                type="number",
                unit=Unit.SQUARE_METRE,
                description="Area fraction.",
                derivations=[
                    FractionOfLocationArea(
                        name="fraction", description="Area fraction."
                    )
                ],
            )
        ]
    ),
)
def work(
    location: LocationInput, parameters: WorkParameters, context: RunContext
) -> pd.DataFrame:
    """Run silent work, recording process-local resource identity for tests.

    Returns:
        Fractional area values indexed by the original feature IDs.

    Raises:
        RuntimeError: When exercising an unexpected plugin crash.
        OperationalError: When exercising a database outage.
    """
    with get_worker_engine().connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
    (context.temp_dir / "pid").write_text(str(os.getpid()), encoding="utf-8")
    context.report_progress(stage="computing", current=0, total=1)
    time.sleep(parameters.seconds)
    if parameters.outcome == "crash":
        msg = "Private diagnostic detail must never appear in public responses."
        raise RuntimeError(msg)
    if parameters.outcome == "database":
        statement = "SELECT private"
        raise OperationalError(statement, {}, ConnectionError("offline"))
    areas = calculate_feature_areas_m2(location)
    values = [
        "invalid"
        if parameters.outcome == "invalid"
        else areas[feature.id] * parameters.fraction
        for feature in location.features
    ]
    return pd.DataFrame(
        {"area": values}, index=[feature.id for feature in location.features]
    )


def create_plugin() -> PluginDefinition:
    """Return the full domain fixture and synthetic lifecycle metric."""
    return PluginDefinition(
        metrics=[
            capacity,
            reused_capacity,
            selection_size,
            bounded_features,
            feature_report,
            interval_width,
            work,
        ]
    )
