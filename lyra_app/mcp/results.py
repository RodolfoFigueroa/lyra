"""Bounded MCP result projections and authenticated HTTP handoffs."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit, urlunsplit

from lyra.sdk.models.job import (
    FileJobResult,
    ResultColumnSummary,
    ResultSummary,
    ResultTableMetadata,
    ResultTablePreview,
    TableJobResult,
    build_table_preview,
    build_table_summary,
    result_ref_for_job,
)
from lyra.sdk.models.plugin import (
    TableOutput,
    effective_table_columns,
)

from lyra_app.mcp.models import (
    ActiveResultOutput,
    CompactProvenance,
    LyraAPIHandoffOutput,
    ResultFileOutput,
    TerminalResultOutput,
    TruncationOutput,
)

if TYPE_CHECKING:
    from lyra_app.job_observation import JobObservation

MAX_PREVIEW_ROWS = 10
MAX_DATA_COLUMNS = 20
MAX_DISPLAY_CHARACTERS = 500


def handoff(base_url: str, job_id: str, suffix: str) -> LyraAPIHandoffOutput:
    """Build authenticated access metadata without exposing credentials.

    Returns:
        An absolute URL preserving the configured base path.
    """
    base = urlsplit(base_url)
    path = f"{base.path.rstrip('/')}/jobs/{quote(job_id, safe='-._~')}/result/{suffix}"
    return LyraAPIHandoffOutput.model_validate(
        {
            "method": "GET",
            "url": urlunsplit((base.scheme, base.netloc, path, "", "")),
            "authentication": {
                "scheme": "Bearer",
                "credential_env_var": "LYRA_AGENT_API_KEY",
            },
        }
    )


def _display(value: str, truncation: TruncationOutput) -> str:
    if len(value) <= MAX_DISPLAY_CHARACTERS:
        return value
    truncation.shortened_strings += 1
    return value[: MAX_DISPLAY_CHARACTERS - 1] + "…"


def _compact_crs(value: object) -> object:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            name = properties.get("name")
            if isinstance(name, str) and len(name) <= MAX_DISPLAY_CHARACTERS:
                return name
    if isinstance(value, str) and len(value) <= MAX_DISPLAY_CHARACTERS:
        return value
    return None


def _compact_input(value: object) -> object:
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and value.get("type") in {
            "FeatureCollection",
            "Feature",
            "Point",
            "Polygon",
            "MultiPolygon",
            "LineString",
            "MultiPoint",
            "MultiLineString",
            "GeometryCollection",
        }:
            return {
                "type": value["type"],
                "feature_count": len(value.get("features", []))
                if value["type"] == "FeatureCollection"
                else 1,
                "crs": _compact_crs(value.get("crs")),
            }
        if (
            "value" in value
            and isinstance(value["value"], dict)
            and "type" in value["value"]
        ):
            return _compact_input(value["value"])
        return {"type": "object", "count": len(value)}
    if isinstance(value, list):
        return {"type": "array", "count": len(value)}
    if isinstance(value, str) and len(value) > MAX_DISPLAY_CHARACTERS:
        return {"type": "string", "characters": len(value)}
    return value


def _error_details(
    error: dict[str, Any] | None, truncation: TruncationOutput
) -> dict[str, Any] | None:
    if error is None:
        return None
    compact: dict[str, Any] = {}
    for key, value in error.items():
        if len(key) > MAX_DISPLAY_CHARACTERS:
            truncation.omitted_sections.append("error field")
        elif isinstance(value, str):
            # Codes and field names are identifiers, never abbreviate them.
            if key in {"code", "type", "field"}:
                compact[key] = value
            else:
                compact[key] = _display(value, truncation)
        elif isinstance(value, dict | list):
            compact[key] = _compact_input(value)
            truncation.omitted_sections.append("error details")
        else:
            compact[key] = value
    return compact


def project_observation(
    job_id: str, observation: JobObservation, base_url: str
) -> ActiveResultOutput | TerminalResultOutput:
    """Build bounded previews directly from the retained payload.

    Returns:
        A typed active or terminal projection; callers handle missing results.
    """
    snapshot, result = observation.snapshot, observation.result
    truncation = TruncationOutput()
    if (
        result is None
        and snapshot is not None
        and snapshot.status in {"queued", "running"}
    ):
        progress = (
            snapshot.progress.model_copy(deep=True) if snapshot.progress else None
        )
        if progress and progress.message:
            progress.message = _display(progress.message, truncation)
        return ActiveResultOutput.model_validate(
            {
                "job_id": job_id,
                "result_ref": result_ref_for_job(job_id),
                "status": snapshot.status,
                "metric": snapshot.metric,
                "created_at": snapshot.created_at,
                "updated_at": snapshot.updated_at,
                "started_at": snapshot.started_at,
                "progress": progress,
                "truncation": truncation,
            }
        )
    status = result.status if result else snapshot.status if snapshot else None
    error = (
        getattr(result, "error", None)
        if result
        else snapshot.error
        if snapshot
        else None
    )
    output = TerminalResultOutput.model_validate(
        {
            "job_id": job_id,
            "result_ref": result_ref_for_job(job_id),
            "status": status,
            "result_kind": result.kind if result else status,
            "completed_at": snapshot.completed_at
            if snapshot and snapshot.status == status
            else None,
            "lifetime": observation.lifetime,
            "descriptor": handoff(base_url, job_id, "descriptor"),
            "error": _error_details(error, truncation),
            "truncation": truncation,
        }
    )
    provenance = observation.provenance
    if provenance:
        output.provenance = CompactProvenance(
            metric=provenance.metric,
            catalog_fingerprint=provenance.catalog_fingerprint,
            plugin=provenance.plugin,
            created_at=provenance.created_at,
            row_identity=provenance.row_identity,
            input={
                key: _compact_input(value) for key, value in provenance.input.items()
            },
        )
        truncation.omitted_sections.append("provenance.output")
        if output.provenance.input != provenance.input:
            truncation.omitted_sections.append("provenance.input details")
    if isinstance(result, TableJobResult):
        _project_table(result, observation, output)
    elif isinstance(result, FileJobResult):
        output.file = ResultFileOutput(
            media_type=result.media_type, download=handoff(base_url, job_id, "download")
        )
        output.summary = ResultSummary(kind="file")
    elif result:
        output.summary = ResultSummary(kind=result.kind)
    truncation.omitted_sections = list(dict.fromkeys(truncation.omitted_sections))
    return output


def _project_table(
    result: TableJobResult, observation: JobObservation, output: TerminalResultOutput
) -> None:
    truncation = output.truncation
    positions = [
        position
        for position, column in enumerate(result.columns)
        if len(column) <= MAX_DISPLAY_CHARACTERS
    ][:MAX_DATA_COLUMNS]
    columns = [result.columns[position] for position in positions]
    truncation.omitted_columns = len(result.columns) - len(columns)
    index_field = build_table_preview(result, row_limit=0).index_field
    # Slice columns before generating either statistics or preview objects.
    if columns:
        selected = TableJobResult(
            job_id=result.job_id,
            columns=columns,
            index=result.index,
            data=[[row[position] for position in positions] for row in result.data],
        )
        output.summary = _table_summary(selected, truncation)
        output.summary.column_count = len(result.columns)
    else:
        output.summary = ResultSummary(
            kind="table", row_count=len(result.index), column_count=len(result.columns)
        )
    rows = []
    for index, values in zip(result.index, result.data, strict=True):
        if len(index) > MAX_DISPLAY_CHARACTERS:
            continue
        row: dict[str, Any] = {index_field: index}
        for position in positions:
            value = values[position]
            if isinstance(value, dict | list):
                truncation.omitted_sections.append("preview collection cells")
                continue
            row[result.columns[position]] = (
                _display(value, truncation) if isinstance(value, str) else value
            )
        rows.append(row)
        if len(rows) == MAX_PREVIEW_ROWS:
            break
    truncation.omitted_rows = len(result.index) - len(rows)
    output.preview = ResultTablePreview(
        index_field=index_field,
        rows=rows,
        row_limit=MAX_PREVIEW_ROWS,
        truncated=truncation.omitted_rows > 0,
    )
    contracts = []
    provenance = observation.provenance
    if provenance and isinstance(provenance.output, TableOutput):
        expanded = effective_table_columns(provenance.output)
        by_name = {column.name: column for column in expanded}
        for name in columns:
            column = by_name[name].model_copy(deep=True)
            if column.derivations:
                column.derivations = []
                truncation.omitted_sections.append("table.column_contracts.derivations")
            column.description = _display(column.description, truncation)
            column.unit = _display(column.unit, truncation)
            contracts.append(column)
    output.table = ResultTableMetadata(
        row_count=len(result.index),
        column_count=len(result.columns),
        columns=columns,
        column_contracts=contracts,
        index_field=index_field,
        row_identity=provenance.row_identity if provenance else None,
    )


def _table_summary(
    result: TableJobResult, truncation: TruncationOutput
) -> ResultSummary:
    try:
        summary = build_table_summary(result)
    except OverflowError:
        # Raw integers remain exact even when SDK floating-point statistics overflow.
        columns = []
        for position, name in enumerate(result.columns):
            null_count = sum(
                value is None or (isinstance(value, float) and not math.isfinite(value))
                for row in result.data
                for value in [row[position]]
            )
            columns.append(
                ResultColumnSummary(
                    name=name,
                    count=len(result.index) - null_count,
                    null_count=null_count,
                )
            )
        truncation.omitted_sections.append("summary.numeric")
        return ResultSummary(
            kind="table",
            row_count=len(result.index),
            column_count=len(result.columns),
            columns=columns,
        )
    for column in summary.columns:
        if (
            column.numeric
            and column.numeric.mean is not None
            and not math.isfinite(column.numeric.mean)
        ):
            column.numeric.mean = None
            truncation.omitted_sections.append("summary.numeric.mean")
    return summary
