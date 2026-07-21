"""Shared HTTP exception factories and error responses."""

from fastapi import HTTPException, status

from lyra_app.config import LyraConfig


def database_unavailable_http_exception(config: LyraConfig) -> HTTPException:
    """Build the standard retryable spatial-database HTTP error.

    Returns:
        A 503 exception with structured details and a ``Retry-After`` header.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "database_unavailable",
            "message": "The spatial database is temporarily unavailable.",
            "retryable": True,
        },
        headers={"Retry-After": str(config.database.retry_after_seconds)},
    )


__all__ = ["database_unavailable_http_exception"]
