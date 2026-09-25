"""Service-independent normalization of native plugin return values."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    NotRequired,
    Protocol,
    TypedDict,
    Unpack,
    runtime_checkable,
)

from lyra.sdk.errors import MetricResultError
from lyra.sdk.models.job import FileJobResult, TableJobResult
from lyra.sdk.models.plugin import FileOutput, OutputSpec, TableColumn, TableOutput

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from lyra.sdk.models.geometry import GeoJSON
    from lyra.sdk.types import JsonScalar


class ResultOptions(TypedDict):
    """Explicit execution data supplied by the caller of result normalization."""

    job_id: str
    location: NotRequired[GeoJSON | None]
    temp_dir: NotRequired[Path | None]
    location_areas_m2: NotRequired[Mapping[str, float] | None]


_FRACTION_TOLERANCE = 1e-9


class ColumnLike(Protocol):
    """Column extraction needed to preserve a frame's individual scalar types."""

    def tolist(self) -> list[object]:
        """Return this column's values in row order."""
        ...


@runtime_checkable
class NativeDataFrame(Protocol):
    """Minimal DataFrame surface, without a pandas runtime dependency."""

    @property
    def index(self) -> Iterable[object]:
        """The existing row labels."""
        ...

    @property
    def columns(self) -> Iterable[object]:
        """The existing column labels."""
        ...

    def __getitem__(self, name: str) -> ColumnLike:
        """Select one column without whole-frame dtype promotion."""
        ...


def _scalar(value: object) -> object:
    # NumPy object columns can retain scalar wrappers after Series.tolist().
    # Restrict extraction to numerical scalars, never arbitrary .item() objects.
    if type(value).__module__.split(".")[0] == "numpy":
        dtype = getattr(value, "dtype", None)
        if (
            getattr(dtype, "kind", None) in {"b", "i", "u", "f"}
            and type(value).__name__ != "ndarray"
        ):
            item = getattr(value, "item", None)
            if callable(item):
                return item()
    return value


def _cell(value: object, column: TableColumn, metric: str, path: str) -> JsonScalar:
    value = _scalar(value)
    if type(value) is float and math.isnan(value):
        value = None
    if value is None:
        if column.nullable:
            return None
        raise MetricResultError(metric, path, "null is not allowed")
    if type(value) is float and not math.isfinite(value):
        raise MetricResultError(metric, path, "numbers must be finite")
    accepted = {
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "string": (str,),
    }
    if type(value) not in accepted[column.type]:
        raise MetricResultError(
            metric, path, f"expected {column.type}; received {type(value).__name__}"
        )
    # The runtime checks above exclude non-JSON objects and scalar coercion.
    if isinstance(value, str | bool | int | float):
        return value
    raise MetricResultError(metric, path, "unsupported scalar")


@dataclass
class _TableNormalizer:
    metric: str
    output: TableOutput
    job_id: str
    location: GeoJSON
    areas: Mapping[str, float] | None

    def normalize(self, frame: NativeDataFrame) -> TableJobResult:
        index = list(frame.index)
        columns = list(frame.columns)
        expected_index = [feature.id for feature in self.location.features]
        if (
            any(type(value) is not str for value in index)
            or index != expected_index
            or len(set(expected_index)) != len(expected_index)
        ):
            raise MetricResultError(
                self.metric,
                "index",
                "unique string index must exactly match location feature IDs in order",
            )
        names = [column.name for column in self.output.columns]
        if any(type(value) is not str for value in columns) or columns != names:
            raise MetricResultError(
                self.metric,
                "columns",
                "string columns must exactly match declared source columns in order",
            )
        values_by_column = [
            self._column_values(frame, column, len(index))
            for column in self.output.columns
        ]
        source_rows = [list(row) for row in zip(*values_by_column, strict=True)]
        names, rows = self._derive(expected_index, names, source_rows)
        return TableJobResult(
            job_id=self.job_id, index=expected_index, columns=names, data=rows
        )

    def _column_values(
        self,
        frame: NativeDataFrame,
        column: TableColumn,
        row_count: int,
    ) -> list[JsonScalar]:
        values = frame[column.name].tolist()
        if not isinstance(values, list) or len(values) != row_count:
            raise MetricResultError(
                self.metric,
                f"column {column.name!r}",
                "column length must match index length",
            )
        return [
            _cell(value, column, self.metric, f"row {row}, column {column.name!r}")
            for row, value in enumerate(values)
        ]

    def _derive(
        self,
        index: list[str],
        names: list[str],
        rows: list[list[JsonScalar]],
    ) -> tuple[list[str], list[list[JsonScalar]]]:
        if not any(column.derivations for column in self.output.columns):
            return names, rows
        areas = self.areas
        if areas is None or list(areas) != index:
            raise MetricResultError(
                self.metric,
                "location_areas_m2",
                "areas must match location feature IDs in order",
            )
        for feature_id, area in areas.items():
            if (
                isinstance(area, bool)
                or not isinstance(area, int | float)
                or not math.isfinite(area)
                or area <= 0
            ):
                raise MetricResultError(
                    self.metric,
                    f"location_areas_m2.{feature_id}",
                    "area must be finite and positive",
                )
        derived_names: list[str] = []
        derived_rows: list[list[JsonScalar]] = [[] for _ in rows]
        for position, column in enumerate(self.output.columns):
            derived_names.append(column.name)
            derived_names.extend(item.name for item in column.derivations)
            for row_index, (feature_id, source_row) in enumerate(
                zip(index, rows, strict=True)
            ):
                value = source_row[position]
                derived_rows[row_index].append(value)
                for item in column.derivations:
                    path = f"row {row_index}, column {item.name!r}"
                    derived_rows[row_index].append(
                        self._fraction(value, areas[feature_id], path)
                    )
        return derived_names, derived_rows

    def _fraction(self, value: JsonScalar, area: float, path: str) -> float | None:
        if value is None:
            return None
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise MetricResultError(self.metric, path, "area source must be numeric")
        fraction = value / area
        if (
            not math.isfinite(fraction)
            or not -_FRACTION_TOLERANCE <= fraction <= 1 + _FRACTION_TOLERANCE
        ):
            raise MetricResultError(
                self.metric, path, "derived fraction is outside [0, 1]"
            )
        return min(1.0, max(0.0, fraction))


def _file_result(
    metric: str,
    output: FileOutput,
    value: object,
    *,
    job_id: str,
    temp_dir: Path | None,
) -> FileJobResult:
    if not isinstance(value, Path) or temp_dir is None:
        raise MetricResultError(
            metric, "file", "file output requires a Path and temp_dir"
        )
    path = (value if value.is_absolute() else temp_dir / value).resolve()
    if not path.is_relative_to(temp_dir.resolve()) or not path.is_file():
        raise MetricResultError(
            metric,
            "file",
            "file must exist inside temp_dir, including after symlink resolution",
        )
    if path.suffix.lower() not in {
        extension.lower() for extension in output.extensions
    }:
        raise MetricResultError(
            metric, "file", "file suffix must match a declared extension"
        )
    return FileJobResult(
        job_id=job_id, file_path=str(path), media_type=output.media_type
    )


def normalize_native_result(
    metric: str,
    output: OutputSpec,
    value: object,
    **options: Unpack[ResultOptions],
) -> TableJobResult | FileJobResult:
    """Normalize a native result using explicit, service-independent inputs.

    Returns:
        The validated terminal success model with platform-owned metadata.

    Raises:
        MetricResultError: If extraction, scalar validation, or file checks fail.
    """
    location = options.get("location")
    if isinstance(output, TableOutput) and (
        location is None or not isinstance(value, NativeDataFrame)
    ):
        raise MetricResultError(
            metric, "table", "table output requires a DataFrame and resolved location"
        )
    try:
        if isinstance(output, FileOutput):
            return _file_result(
                metric,
                output,
                value,
                job_id=options["job_id"],
                temp_dir=options.get("temp_dir"),
            )
        if location is not None and isinstance(value, NativeDataFrame):
            return _TableNormalizer(
                metric,
                output,
                options["job_id"],
                location,
                options.get("location_areas_m2"),
            ).normalize(value)
    except MetricResultError:
        raise
    except (
        AttributeError,
        TypeError,
        ValueError,
        IndexError,
        KeyError,
        OverflowError,
        OSError,
    ) as exc:
        raise MetricResultError(metric, "result", str(exc)) from exc

    raise MetricResultError(metric, "result", "unsupported result")
