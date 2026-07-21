"""Asynchronous service mutations initiated by the terminal interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.sdk.models import PluginCatalogRefreshStatus
    from lyra.tui.client import LyraTuiClient


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome shown after an operator-triggered mutation."""

    succeeded: bool
    message: str
    refresh_after: bool = False


class ActionService:
    """Perform authenticated operator actions and normalize their outcomes."""

    def __init__(self, client: LyraTuiClient) -> None:
        """Initialize the action service with an administrative client."""
        self.client = client

    async def cancel_job(self, job_id: str) -> ActionResult:
        """Cancel a job and return a display-ready outcome.

        Returns:
            The cancellation result or a normalized failure message.
        """
        try:
            response = await self.client.cancel_admin_job(job_id)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Cancel job", exc)
        return ActionResult(
            succeeded=True,
            message=(
                f"Cancelled job {response.job_id}; "
                f"revoke requested: {response.revoke_requested}."
            ),
            refresh_after=True,
        )

    async def restart_workers(self, *, restart_timeout: float) -> ActionResult:
        """Request a worker restart and return its operator-facing outcome.

        Returns:
            The restart result or a normalized failure message.
        """
        try:
            response = await self.client.restart_workers(
                restart_timeout=restart_timeout
            )
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Restart workers", exc)
        return ActionResult(
            succeeded=True,
            message=response.message,
            refresh_after=True,
        )

    async def create_plugin_repo(
        self,
        *,
        source: str,
        repo_id: str | None,
    ) -> ActionResult:
        """Register a plugin repository and refresh the catalog.

        Returns:
            The repository operation and catalog-refresh outcome.
        """
        try:
            response = await self.client.create_plugin_repo(source, repo_id=repo_id)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Add plugin repo", exc)
        return ActionResult(
            succeeded=True,
            message=_with_catalog_refresh_status(
                f"Added plugin repo {response.repo.id}.",
                response.catalog_refresh,
            ),
            refresh_after=True,
        )

    async def set_plugin_repo_enabled(
        self,
        *,
        repo_id: str,
        enabled: bool,
    ) -> ActionResult:
        """Enable or disable a plugin repository.

        Returns:
            The updated state and catalog-refresh outcome.
        """
        try:
            response = await self.client.update_plugin_repo(repo_id, enabled=enabled)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Update plugin repo", exc)
        state = "enabled" if response.repo.enabled else "disabled"
        return ActionResult(
            succeeded=True,
            message=_with_catalog_refresh_status(
                f"Plugin repo {response.repo.id} {state}.",
                response.catalog_refresh,
            ),
            refresh_after=True,
        )

    async def delete_plugin_repo(self, repo_id: str) -> ActionResult:
        """Delete a plugin repository and report affected catalog state.

        Returns:
            The deletion and catalog-refresh outcome.
        """
        try:
            response = await self.client.delete_plugin_repo(repo_id)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Delete plugin repo", exc)
        return ActionResult(
            succeeded=response.deleted,
            message=_with_catalog_refresh_status(
                f"Deleted plugin repo {response.repo_id}.",
                response.catalog_refresh,
            ),
            refresh_after=response.deleted,
        )

    async def sync_plugin_repo(self, repo_id: str) -> ActionResult:
        """Synchronize a plugin repository with its configured source.

        Returns:
            The synchronization and catalog-refresh outcome.
        """
        try:
            response = await self.client.sync_plugin_repo(repo_id)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Sync plugin repo", exc)
        changed = "changed" if response.changed else "unchanged"
        return ActionResult(
            succeeded=True,
            message=_with_catalog_refresh_status(
                f"Synced {response.display_name} ({changed}).",
                response.catalog_refresh,
            ),
            refresh_after=True,
        )

    async def refresh_plugin_catalog(self) -> ActionResult:
        """Refresh the plugin catalog and summarize worker restart guidance.

        Returns:
            The catalog refresh outcome.
        """
        try:
            response = await self.client.refresh_plugin_catalog()
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Refresh catalog", exc)
        restart = (
            "restart recommended"
            if response.workers_restart_recommended
            else "restart not required"
        )
        return ActionResult(
            succeeded=True,
            message=f"{response.message} {restart}.",
            refresh_after=True,
        )

    async def set_plugin_routing(
        self,
        *,
        metric_name: str,
        queue: str,
    ) -> ActionResult:
        """Assign a metric to an execution queue.

        Returns:
            The routing assignment outcome.
        """
        try:
            response = await self.client.set_plugin_routing(metric_name, queue)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Assign route", exc)
        return ActionResult(
            succeeded=True,
            message=f"Assigned {response.metric_name} to {response.queue}.",
            refresh_after=True,
        )

    async def delete_plugin_routing(self, metric_name: str) -> ActionResult:
        """Remove a metric's explicit queue assignment.

        Returns:
            The routing deletion outcome.
        """
        try:
            response = await self.client.delete_plugin_routing(metric_name)
        except Exception as exc:  # ruff:ignore[blind-except]
            return _failure("Delete route", exc)
        verb = "Deleted" if response.deleted else "No explicit route for"
        return ActionResult(
            succeeded=True,
            message=f"{verb} {response.metric_name}.",
            refresh_after=response.deleted,
        )


def _failure(operation: str, exc: Exception) -> ActionResult:
    message = str(exc) or exc.__class__.__name__
    return ActionResult(
        succeeded=False,
        message=f"{operation} failed: {message}",
    )


def _with_catalog_refresh_status(
    message: str,
    status: PluginCatalogRefreshStatus,
) -> str:
    if status.refreshed:
        return message
    error = status.error or "unknown error"
    return f"{message} Catalog refresh failed: {error}"
