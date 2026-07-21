"""Read and administrative client adapters used by the Lyra TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lyra.api import AsyncLyraAdminClient

if TYPE_CHECKING:
    from lyra.sdk.models import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        CreatePluginRepoResponse,
        DeleteMetricQueueResponse,
        DeletePluginRepoResponse,
        JobCancelResponse,
        JobListResponse,
        MetricQueueAssignmentResponse,
        PluginCatalogRefreshResponse,
        PluginRepoListResponse,
        PluginRoutingResponse,
        QueuesResponse,
        ReadinessResponse,
        SyncPluginRepoResponse,
        UpdatePluginRepoResponse,
        WorkerRestartResponse,
        WorkersResponse,
    )
    from lyra.tui.config import TuiConfig


class LyraTuiReadClient(Protocol):
    """Describe read operations required to refresh the terminal interface."""

    async def get_readiness(self) -> ReadinessResponse:
        """Return service dependency readiness."""
        ...

    async def get_admin_status(self) -> AdminStatusResponse:
        """Return the aggregate administrative service status."""
        ...

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        """Return a non-secret summary of the active configuration."""
        ...

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        """Return a summary of the active plugin catalog."""
        ...

    async def get_admin_workers(self) -> WorkersResponse:
        """Return the current worker-pool state."""
        ...

    async def get_admin_queues(self) -> QueuesResponse:
        """Return configured queues and their worker coverage."""
        ...

    async def list_admin_jobs(self) -> JobListResponse:
        """Return retained jobs visible to an administrator."""
        ...

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        """Return configured plugin repositories."""
        ...

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        """Return explicit metric-to-queue assignments."""
        ...


class LyraTuiClient(LyraTuiReadClient, Protocol):
    """Describe administrative mutations available to the terminal interface."""

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        """Request cancellation of a retained job.

        Returns:
            The job state and worker-revocation request status.
        """
        ...

    async def restart_workers(
        self, *, restart_timeout: float = 30.0
    ) -> WorkerRestartResponse:
        """Restart worker pools after waiting for active tasks to drain.

        Returns:
            The worker restart request outcome.
        """
        ...

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        """Create and persist a plugin repository definition.

        Returns:
            The created repository and catalog refresh status.
        """
        ...

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        """Update mutable fields on a plugin repository definition.

        Returns:
            The updated repository and catalog refresh status.
        """
        ...

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        """Delete a plugin repository definition.

        Returns:
            The deletion and catalog refresh outcome.
        """
        ...

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        """Synchronize one repository into the plugin catalog.

        Returns:
            The repository synchronization and catalog refresh outcome.
        """
        ...

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        """Refresh the catalog from every enabled plugin repository.

        Returns:
            The updated catalog, routing, and worker restart status.
        """
        ...

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        """Assign a metric to an allowed worker queue.

        Returns:
            The confirmed metric-to-queue assignment.
        """
        ...

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse:
        """Remove the explicit queue assignment for a metric.

        Returns:
            Whether an assignment was removed and the affected metric name.
        """
        ...


class LyraApiClientAdapter:
    """Thin async adapter around the public Lyra API client."""

    def __init__(self, config: TuiConfig) -> None:
        """Create an administrative API client from terminal configuration."""
        self._client = AsyncLyraAdminClient(
            config.host,
            timeout=config.timeout,
            admin_api_key=config.admin_api_key,
            secure=config.secure,
        )

    async def get_readiness(self) -> ReadinessResponse:
        """Return service dependency readiness."""
        return await self._client.health.readiness()

    async def get_admin_status(self) -> AdminStatusResponse:
        """Return the aggregate administrative service status."""
        return await self._client.status()

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        """Return a non-secret summary of the active configuration."""
        return await self._client.config_summary()

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        """Return a summary of the active plugin catalog."""
        return await self._client.catalog.summary()

    async def get_admin_workers(self) -> WorkersResponse:
        """Return the current worker-pool state."""
        return await self._client.workers.list()

    async def get_admin_queues(self) -> QueuesResponse:
        """Return configured queues and their worker coverage."""
        return await self._client.queues.list()

    async def list_admin_jobs(self) -> JobListResponse:
        """Return retained jobs visible to an administrator."""
        return await self._client.jobs.list()

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        """Return configured plugin repositories."""
        return await self._client.plugin_repos.list()

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        """Return explicit metric-to-queue assignments."""
        return await self._client.routing.list()

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        """Request cancellation of a retained job.

        Returns:
            The job state and worker-revocation request status.
        """
        return await self._client.jobs.cancel(job_id)

    async def restart_workers(
        self,
        *,
        restart_timeout: float = 30.0,
    ) -> WorkerRestartResponse:
        """Restart worker pools after waiting for active tasks to drain.

        Returns:
            The worker restart request outcome.
        """
        return await self._client.workers.restart(timeout=restart_timeout)

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
        """Create and persist a plugin repository definition.

        Returns:
            The created repository and catalog refresh status.
        """
        return await self._client.plugin_repos.create(
            source,
            repo_id=repo_id,
            enabled=enabled,
        )

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse:
        """Update mutable fields on a plugin repository definition.

        Returns:
            The updated repository and catalog refresh status.
        """
        return await self._client.plugin_repos.update(
            repo_id,
            source=source,
            enabled=enabled,
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        """Delete a plugin repository definition.

        Returns:
            The deletion and catalog refresh outcome.
        """
        return await self._client.plugin_repos.delete(repo_id)

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        """Synchronize one repository into the plugin catalog.

        Returns:
            The repository synchronization and catalog refresh outcome.
        """
        return await self._client.plugin_repos.sync(repo_id)

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        """Refresh the catalog from every enabled plugin repository.

        Returns:
            The updated catalog, routing, and worker restart status.
        """
        return await self._client.catalog.refresh()

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        """Assign a metric to an allowed worker queue.

        Returns:
            The confirmed metric-to-queue assignment.
        """
        return await self._client.routing.set(metric_name, queue)

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse:
        """Remove the explicit queue assignment for a metric.

        Returns:
            Whether an assignment was removed and the affected metric name.
        """
        return await self._client.routing.delete(metric_name)
