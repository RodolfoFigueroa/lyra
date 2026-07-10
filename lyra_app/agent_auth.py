from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Annotated

from fastapi import Header, HTTPException, status
from starlette.responses import JSONResponse

from lyra_app.config import ConfigLoadError, ConfigSecretError, get_config

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_AUTHENTICATE_HEADERS = {"WWW-Authenticate": "Bearer"}


def validate_agent_authorization(authorization: str | None, expected_key: str) -> None:
    """Validate one Agent API Bearer credential without exposing key material."""
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent bearer token is required.",
            headers=_AUTHENTICATE_HEADERS,
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent bearer token is malformed.",
            headers=_AUTHENTICATE_HEADERS,
        )
    if not hmac.compare_digest(parts[1].encode(), expected_key.encode()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid agent bearer token.",
        )


def require_agent_key(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """FastAPI dependency enforcing the shared Agent API credential."""
    try:
        expected_key = get_config().agent.read_api_key()
    except (ConfigLoadError, ConfigSecretError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent API key is not configured on the server.",
        ) from exc
    validate_agent_authorization(authorization, expected_key)


class AgentBearerAuthMiddleware:
    """ASGI middleware applying the same Agent API check used by REST jobs."""

    def __init__(self, app: ASGIApp, *, agent_api_key: str) -> None:
        self._app = app
        self._agent_api_key = agent_api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"").decode("latin-1") or None
        try:
            validate_agent_authorization(authorization, self._agent_api_key)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )(scope, receive, send)
            return

        await self._app(scope, receive, send)


__all__ = [
    "AgentBearerAuthMiddleware",
    "require_agent_key",
    "validate_agent_authorization",
]
