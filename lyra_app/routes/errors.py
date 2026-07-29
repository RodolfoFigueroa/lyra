"""Shared HTTP exception factories and error responses."""

from fastapi import HTTPException, status
from lyra.sdk.db import (
    DatabaseQueryError,
    DatabaseQueryTimeoutError,
    DatabaseUnavailableError,
    LyraDatabaseError,
)

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
            "message": DatabaseUnavailableError.public_message,
            "retryable": True,
        },
        headers={"Retry-After": str(config.database.retry_after_seconds)},
    )


def database_error_http_exception(
    error: LyraDatabaseError,
    config: LyraConfig,
) -> HTTPException:
    """Build a safe HTTP response for one SDK database failure.

    Returns:
        A structured HTTP exception with stable code and retryability.
    """
    if isinstance(error, DatabaseUnavailableError):
        return database_unavailable_http_exception(config)
    if isinstance(error, DatabaseQueryTimeoutError):
        return HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": error.code,
                "message": error.public_message,
                "retryable": error.retryable,
            },
            headers={"Retry-After": str(config.database.retry_after_seconds)},
        )
    if isinstance(error, DatabaseQueryError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": error.code,
                "message": error.public_message,
                "retryable": error.retryable,
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": error.code,
            "message": error.public_message,
            "retryable": error.retryable,
        },
    )


__all__ = ["database_error_http_exception", "database_unavailable_http_exception"]
