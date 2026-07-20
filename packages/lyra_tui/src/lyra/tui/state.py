from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, TypeVar

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
    kind: ErrorKind
    message: str

    @classmethod
    def from_exception(cls, exc: Exception, *, context: str) -> TuiError:
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
        return self.admin_status is not None

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


@dataclass(slots=True)
class LyraTuiState:
    client: LyraTuiReadClient
    has_admin_key: bool
    snapshot: TuiSnapshot = field(default_factory=TuiSnapshot)

    async def refresh(self) -> TuiSnapshot:
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

    config_summary, config_error = await _capture(
        client.get_admin_config_summary(),
        context="Fetch config summary",
    )
    catalog, catalog_error = await _capture(
        client.get_admin_catalog(),
        context="Fetch catalog",
    )
    workers, workers_error = await _capture(
        client.get_admin_workers(),
        context="Fetch workers",
    )
    queues, queues_error = await _capture(
        client.get_admin_queues(),
        context="Fetch queues",
    )
    jobs, jobs_error = await _capture(
        client.list_admin_jobs(),
        context="Fetch jobs",
    )
    plugin_repos, repos_error = await _capture(
        client.list_plugin_repos(),
        context="Fetch plugin repos",
    )
    plugin_routing, routing_error = await _capture(
        client.list_plugin_routing(),
        context="Fetch plugin routing",
    )
    errors = tuple(
        error
        for error in (
            config_error,
            catalog_error,
            workers_error,
            queues_error,
            jobs_error,
            repos_error,
            routing_error,
        )
        if error is not None
    )

    return TuiSnapshot(
        phase="partial" if errors else "ready",
        readiness=readiness,
        admin_status=admin_status,
        config_summary=config_summary,
        catalog=catalog,
        workers=workers,
        queues=queues,
        jobs=jobs,
        plugin_repos=plugin_repos,
        plugin_routing=plugin_routing,
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
    except Exception as exc:  # noqa: BLE001
        return None, TuiError.from_exception(exc, context=context)
