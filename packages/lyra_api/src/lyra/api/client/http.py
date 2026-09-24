"""Private asynchronous JSON requests shared by both polling clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import aiohttp
from lyra.api.client.endpoints import validate_response
from lyra.api.exceptions import DownloadError

if TYPE_CHECKING:
    from lyra.api.client.endpoints import RequestSpec

_ResponseT = TypeVar("_ResponseT")


async def request_json(
    spec: RequestSpec[_ResponseT],
    url: str,
    headers: dict[str, str],
    request_seconds: float,
    *,
    exact_timeout: bool = False,
) -> _ResponseT:
    """Perform one JSON request, with exact timeout timing for observations.

    Returns:
        The validated endpoint response.

    Raises:
        DownloadError: If the response cannot be read or the transport fails.
    """
    client_timeout = aiohttp.ClientTimeout(
        total=request_seconds, ceil_threshold=float("inf") if exact_timeout else 5
    )
    try:
        async with (
            aiohttp.ClientSession(timeout=client_timeout) as session,
            session.request(
                spec.method,
                url,
                params=spec.params,
                json=spec.json_body,
                headers=headers,
            ) as response,
        ):
            return validate_response(
                spec,
                response.status,
                await response.text(),
                response.headers.get("content-type", ""),
                response.headers.get("Retry-After"),
            )
    except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
        err = f"Failed to {spec.operation}: request error: {exc}"
        raise DownloadError(
            err,
            retryable=isinstance(exc, (aiohttp.ClientConnectionError, TimeoutError))
            and not isinstance(
                exc,
                (
                    aiohttp.ClientSSLError,
                    aiohttp.ServerFingerprintMismatch,
                    aiohttp.InvalidURL,
                ),
            ),
        ) from exc
