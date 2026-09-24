"""Result, download, and callback policies shared by both clients."""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.api.client.endpoints import check_status, decode_response, get_job_result
from lyra.api.exceptions import DownloadError, MetricRunError
from lyra.sdk.models.job import (
    CancelledJobResult,
    FailedJobResult,
    FileJobResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lyra.sdk.models.job import TableJobResult, TerminalJobResult


def successful_result(result: TerminalJobResult) -> TableJobResult | FileJobResult:
    """Extract a successful terminal result.

    Returns:
        The successful table or file result.

    Raises:
        MetricRunError: If the job failed or was cancelled.
    """
    if isinstance(result, FailedJobResult | CancelledJobResult):
        raise MetricRunError(result)
    return result


def require_file_result(result: TableJobResult | FileJobResult) -> FileJobResult:
    """Validate that a successful metric produced a file.

    Returns:
        The file result.

    Raises:
        DownloadError: If the result is tabular.
    """
    if not isinstance(result, FileJobResult):
        err = f"Job {result.job_id} did not produce a file result."
        raise DownloadError(err)
    return result


def validate_jsonl_format(value: str) -> None:
    """Validate the requested table-download format.

    Raises:
        DownloadError: If the format is not JSONL.
    """
    if value != "jsonl":
        err = "Only JSONL result downloads are supported. Use format='jsonl'."
        raise DownloadError(err)


def download_needs_text(status: int, content_type: str, job_id: str | None) -> bool:
    """Return whether a download body needs validation before opening a file."""
    return status != 200 or (job_id is not None and "application/json" in content_type)


def validate_download(
    status: int,
    text: str,
    retry_after: str | None,
    *,
    operation: str,
    job_id: str | None,
) -> None:
    """Validate an error or JSON file response before opening the destination.

    Raises:
        DownloadError: If a file endpoint returned a JSON terminal result.
    """
    check_status(status, text, retry_after, operation=operation)
    if job_id is not None:
        result = decode_response(get_job_result(job_id), text)
        err = f"Job {job_id} returned {result.status} JSON result, not a file."
        raise DownloadError(err)


@contextmanager
def dataframe_path() -> Iterator[Path]:
    """Provide a temporary JSONL path and remove it on every exit.

    Yields:
        A closed temporary file that either transport can write.
    """
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as temp_file:
        path = Path(temp_file.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
