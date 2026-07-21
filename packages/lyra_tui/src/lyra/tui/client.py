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
    async def get_readiness(self) -> ReadinessResponse: ...

    async def get_admin_status(self) -> AdminStatusResponse: ...

    async def get_admin_config_summary(self) -> ConfigSummaryResponse: ...

    async def get_admin_catalog(self) -> CatalogSummaryResponse: ...

    async def get_admin_workers(self) -> WorkersResponse: ...

    async def get_admin_queues(self) -> QueuesResponse: ...

    async def list_admin_jobs(self) -> JobListResponse: ...

    async def list_plugin_repos(self) -> PluginRepoListResponse: ...

    async def list_plugin_routing(self) -> PluginRoutingResponse: ...


class LyraTuiClient(LyraTuiReadClient, Protocol):
    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse: ...

    async def restart_workers(
        self, *, restart_timeout: float = 30.0
    ) -> WorkerRestartResponse: ...

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse: ...

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> UpdatePluginRepoResponse: ...

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse: ...

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse: ...

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse: ...

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse: ...

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse: ...


class LyraApiClientAdapter:
    """Thin async adapter around the public Lyra API client."""

    def __init__(self, config: TuiConfig) -> None:
        self._client = AsyncLyraAdminClient(
            config.host,
            timeout=config.timeout,
            admin_api_key=config.admin_api_key,
            secure=config.secure,
        )

    async def get_readiness(self) -> ReadinessResponse:
        return await self._client.health.readiness()

    async def get_admin_status(self) -> AdminStatusResponse:
        return await self._client.status()

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        return await self._client.config_summary()

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        return await self._client.catalog.summary()

    async def get_admin_workers(self) -> WorkersResponse:
        return await self._client.workers.list()

    async def get_admin_queues(self) -> QueuesResponse:
        return await self._client.queues.list()

    async def list_admin_jobs(self) -> JobListResponse:
        return await self._client.jobs.list()

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        return await self._client.plugin_repos.list()

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        return await self._client.routing.list()

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        return await self._client.jobs.cancel(job_id)

    async def restart_workers(
        self,
        *,
        restart_timeout: float = 30.0,
    ) -> WorkerRestartResponse:
        return await self._client.workers.restart(timeout=restart_timeout)

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> CreatePluginRepoResponse:
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
        return await self._client.plugin_repos.update(
            repo_id,
            source=source,
            enabled=enabled,
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        return await self._client.plugin_repos.delete(repo_id)

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        return await self._client.plugin_repos.sync(repo_id)

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        return await self._client.catalog.refresh()

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        return await self._client.routing.set(metric_name, queue)

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse:
        return await self._client.routing.delete(metric_name)
