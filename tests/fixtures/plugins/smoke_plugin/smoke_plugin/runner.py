from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.sdk.models import FileJobResult, JobEnvelope, TableJobResult
from lyra.sdk.models.geometry import GeoJSON

if TYPE_CHECKING:
    from lyra.sdk.context import RunContext


def _feature_ids(job: JobEnvelope) -> list[str]:
    location = GeoJSON.model_validate(job.input["location"])
    return [feature.id for feature in location.features]


def _table_result(job: JobEnvelope, feature_ids: list[str]) -> TableJobResult:
    return TableJobResult.from_mapping(
        job_id=job.job_id,
        input_index=feature_ids,
        columns=["value"],
        values={"value": [job.input["value"] for _feature_id in feature_ids]},
    )


def run_table(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"stage": "table"})
    context.check_cancelled()
    return _table_result(job, _feature_ids(job))


def run_file(job: JobEnvelope, context: RunContext) -> FileJobResult:
    context.emit_event("progress", {"stage": "file"})
    context.check_cancelled()
    feature_ids = _feature_ids(job)
    output_path = context.temp_dir / "smoke-result.txt"
    output_path.write_text(
        "\n".join(["smoke file result", *feature_ids]) + "\n",
        encoding="utf-8",
    )
    return FileJobResult(
        job_id=job.job_id,
        file_path=output_path.name,
        media_type="text/plain",
    )


def run_cancel(job: JobEnvelope, context: RunContext) -> TableJobResult:
    context.emit_event("progress", {"stage": "cancel-check"})
    context.check_cancelled()
    return _table_result(job, _feature_ids(job))
