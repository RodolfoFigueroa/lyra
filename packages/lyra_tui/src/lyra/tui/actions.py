from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.tui.client import LyraTuiClient


@dataclass(frozen=True, slots=True)
class ActionResult:
    succeeded: bool
    message: str
    refresh_after: bool = False


class ActionService:
    def __init__(self, client: LyraTuiClient) -> None:
        self.client = client

    async def cancel_job(self, job_id: str) -> ActionResult:
        try:
            response = await self.client.cancel_admin_job(job_id)
        except Exception as exc:  # noqa: BLE001
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
        try:
            response = await self.client.restart_workers(
                restart_timeout=restart_timeout
            )
        except Exception as exc:  # noqa: BLE001
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
        try:
            response = await self.client.create_plugin_repo(source, repo_id=repo_id)
        except Exception as exc:  # noqa: BLE001
            return _failure("Add plugin repo", exc)
        return ActionResult(
            succeeded=True,
            message=f"Added plugin repo {response.id}.",
            refresh_after=True,
        )

    async def set_plugin_repo_enabled(
        self,
        *,
        repo_id: str,
        enabled: bool,
    ) -> ActionResult:
        try:
            response = await self.client.update_plugin_repo(repo_id, enabled=enabled)
        except Exception as exc:  # noqa: BLE001
            return _failure("Update plugin repo", exc)
        state = "enabled" if response.enabled else "disabled"
        return ActionResult(
            succeeded=True,
            message=f"Plugin repo {response.id} {state}.",
            refresh_after=True,
        )

    async def delete_plugin_repo(self, repo_id: str) -> ActionResult:
        try:
            response = await self.client.delete_plugin_repo(repo_id)
        except Exception as exc:  # noqa: BLE001
            return _failure("Delete plugin repo", exc)
        return ActionResult(
            succeeded=response.deleted,
            message=f"Deleted plugin repo {response.repo_id}.",
            refresh_after=response.deleted,
        )

    async def sync_plugin_repo(self, repo_id: str) -> ActionResult:
        try:
            response = await self.client.sync_plugin_repo(repo_id)
        except Exception as exc:  # noqa: BLE001
            return _failure("Sync plugin repo", exc)
        changed = "changed" if response.changed else "unchanged"
        return ActionResult(
            succeeded=True,
            message=f"Synced {response.display_name} ({changed}).",
            refresh_after=True,
        )

    async def refresh_plugin_catalog(self) -> ActionResult:
        try:
            response = await self.client.refresh_plugin_catalog()
        except Exception as exc:  # noqa: BLE001
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
        try:
            response = await self.client.set_plugin_routing(metric_name, queue)
        except Exception as exc:  # noqa: BLE001
            return _failure("Assign route", exc)
        return ActionResult(
            succeeded=True,
            message=f"Assigned {response.metric_name} to {response.queue}.",
            refresh_after=True,
        )

    async def delete_plugin_routing(self, metric_name: str) -> ActionResult:
        try:
            response = await self.client.delete_plugin_routing(metric_name)
        except Exception as exc:  # noqa: BLE001
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
