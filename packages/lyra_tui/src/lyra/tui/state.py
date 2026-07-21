"""Snapshot and refresh state for the Lyra terminal interface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from lyra.api import DownloadError

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from lyra.sdk.models import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        JobListResponse,
        PluginRepoListResponse,
        PluginRoutingResponse,
        QueuesResponse,
        ReadinessResponse,
        WorkersResponse,
    )
    from lyra.tui.client import LyraTuiReadClient

ErrorKind = Literal["auth", "connection", "api", "unexpected"]
SnapshotPhase = Literal["idle", "loading", "ready", "partial", "error", "auth-required"]
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class TuiError:
    """Categorized error suitable for display in the operator console."""

    kind: ErrorKind
    message: str

    @classmethod
    def from_exception(cls, exc: Exception, *, context: str) -> TuiError:
        """Classify an exception and attach operation context.

        Returns:
            A display-ready authentication, connection, API, or unexpected error.
        """
        message = str(exc) or exc.__class__.__name__
        lower_message = message.lower()
        if isinstance(exc, DownloadError):
            if "http 401" in lower_message or "http 403" in lower_message:
                kind: ErrorKind = "auth"
            elif "request error" in lower_message:
                kind = "connection"
            else:
                kind = "api"
        else:
            kind = "unexpected"
        return cls(kind=kind, message=f"{context}: {message}")


@dataclass(frozen=True, slots=True)
class TuiSnapshot:
    """Point-in-time collection of service data rendered by the TUI."""

    phase: SnapshotPhase = "idle"
    readiness: ReadinessResponse | None = None
    admin_status: AdminStatusResponse | None = None
    config_summary: ConfigSummaryResponse | None = None
    catalog: CatalogSummaryResponse | None = None
    workers: WorkersResponse | None = None
    queues: QueuesResponse | None = None
    jobs: JobListResponse | None = None
    plugin_repos: PluginRepoListResponse | None = None
    plugin_routing: PluginRoutingResponse | None = None
    errors: tuple[TuiError, ...] = ()
    last_updated: datetime | None = None

    @property
    def has_admin_data(self) -> bool:
        """Whether authenticated administrative status was retrieved."""
        return self.admin_status is not None

    @property
    def has_errors(self) -> bool:
        """Whether any snapshot request failed."""
        return bool(self.errors)


@dataclass(slots=True)
class LyraTuiState:
    """Mutable application state holding the latest service snapshot."""

    client: LyraTuiReadClient
    has_admin_key: bool
    snapshot: TuiSnapshot = field(default_factory=TuiSnapshot)

    async def refresh(self) -> TuiSnapshot:
        """Fetch and store a fresh service snapshot.

        Returns:
            The newly stored snapshot.
        """
        self.snapshot = await refresh_snapshot(
            self.client,
            has_admin_key=self.has_admin_key,
        )
        return self.snapshot


async def refresh_snapshot(
    client: LyraTuiReadClient,
    *,
    has_admin_key: bool,
) -> TuiSnapshot:
    """Fetch public and authorized administrative state concurrently.

    Returns:
        A complete, partial, authentication-required, or failed snapshot.
    """
    refreshed_at = datetime.now(UTC)
    readiness, readiness_error = await _capture(
        client.get_readiness(),
        context="Fetch readiness",
    )
    if readiness_error is not None:
        return TuiSnapshot(
            phase="error",
            errors=(readiness_error,),
            last_updated=refreshed_at,
        )
    readiness = cast("ReadinessResponse", readiness)

    if not has_admin_key:
        return TuiSnapshot(
            phase="auth-required",
            readiness=readiness,
            errors=(
                TuiError(
                    kind="auth",
                    message="Admin API key is not configured.",
                ),
            ),
            last_updated=refreshed_at,
        )

    admin_status, admin_status_error = await _capture(
        client.get_admin_status(),
        context="Fetch admin status",
    )
    if admin_status_error is not None:
        return TuiSnapshot(
            phase=("auth-required" if admin_status_error.kind == "auth" else "partial"),
            readiness=readiness,
            errors=(admin_status_error,),
            last_updated=refreshed_at,
        )
    admin_status = cast("AdminStatusResponse", admin_status)

    return await _refresh_admin_snapshot(
        client,
        readiness=readiness,
        admin_status=admin_status,
        refreshed_at=refreshed_at,
    )


async def _refresh_admin_snapshot(
    client: LyraTuiReadClient,
    *,
    readiness: ReadinessResponse,
    admin_status: AdminStatusResponse,
    refreshed_at: datetime,
) -> TuiSnapshot:
    (
        config_result,
        catalog_result,
        workers_result,
        queues_result,
        jobs_result,
        repos_result,
        routing_result,
    ) = await asyncio.gather(
        _capture(client.get_admin_config_summary(), context="Fetch config summary"),
        _capture(client.get_admin_catalog(), context="Fetch catalog"),
        _capture(client.get_admin_workers(), context="Fetch workers"),
        _capture(client.get_admin_queues(), context="Fetch queues"),
        _capture(client.list_admin_jobs(), context="Fetch jobs"),
        _capture(client.list_plugin_repos(), context="Fetch plugin repos"),
        _capture(client.list_plugin_routing(), context="Fetch plugin routing"),
    )
    captured = (
        config_result,
        catalog_result,
        workers_result,
        queues_result,
        jobs_result,
        repos_result,
        routing_result,
    )
    errors = tuple(error for _, error in captured if error is not None)

    return TuiSnapshot(
        phase="partial" if errors else "ready",
        readiness=readiness,
        admin_status=admin_status,
        config_summary=cast("ConfigSummaryResponse | None", config_result[0]),
        catalog=cast("CatalogSummaryResponse | None", catalog_result[0]),
        workers=cast("WorkersResponse | None", workers_result[0]),
        queues=cast("QueuesResponse | None", queues_result[0]),
        jobs=cast("JobListResponse | None", jobs_result[0]),
        plugin_repos=cast("PluginRepoListResponse | None", repos_result[0]),
        plugin_routing=cast("PluginRoutingResponse | None", routing_result[0]),
        errors=errors,
        last_updated=refreshed_at,
    )


async def _capture(
    awaitable: Awaitable[_T],
    *,
    context: str,
) -> tuple[_T | None, TuiError | None]:
    try:
        return await awaitable, None
    except Exception as exc:  # ruff:ignore[blind-except]
        return None, TuiError.from_exception(exc, context=context)
