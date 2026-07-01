from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lyra.api import AsyncLyraAPIClient

if TYPE_CHECKING:
    from lyra.sdk.models import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        DeleteMetricQueueResponse,
        DeletePluginRepoResponse,
        HealthResponse,
        JobCancelResponse,
        JobListResponse,
        MetricQueueAssignmentResponse,
        PluginCatalogRefreshResponse,
        PluginRepoListResponse,
        PluginRepoResponse,
        PluginRoutingResponse,
        QueuesResponse,
        SyncPluginRepoResponse,
        WorkerRestartResponse,
        WorkersResponse,
    )
    from lyra.tui.config import TuiConfig


class LyraTuiReadClient(Protocol):
    async def get_health(self) -> HealthResponse: ...

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
    ) -> PluginRepoResponse: ...

    async def update_plugin_repo(
        self,
        repo_id: str,
        *,
        source: str | None = None,
        enabled: bool | None = None,
    ) -> PluginRepoResponse: ...

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
        self._client = AsyncLyraAPIClient(
            config.host,
            timeout=config.timeout,
            admin_api_key=config.admin_api_key,
            secure=config.secure,
        )

    async def get_health(self) -> HealthResponse:
        return await self._client.get_health()

    async def get_admin_status(self) -> AdminStatusResponse:
        return await self._client.get_admin_status()

    async def get_admin_config_summary(self) -> ConfigSummaryResponse:
        return await self._client.get_admin_config_summary()

    async def get_admin_catalog(self) -> CatalogSummaryResponse:
        return await self._client.get_admin_catalog()

    async def get_admin_workers(self) -> WorkersResponse:
        return await self._client.get_admin_workers()

    async def get_admin_queues(self) -> QueuesResponse:
        return await self._client.get_admin_queues()

    async def list_admin_jobs(self) -> JobListResponse:
        return await self._client.list_admin_jobs()

    async def list_plugin_repos(self) -> PluginRepoListResponse:
        return await self._client.list_plugin_repos()

    async def list_plugin_routing(self) -> PluginRoutingResponse:
        return await self._client.list_plugin_routing()

    async def cancel_admin_job(self, job_id: str) -> JobCancelResponse:
        return await self._client.cancel_admin_job(job_id)

    async def restart_workers(
        self,
        *,
        restart_timeout: float = 30.0,
    ) -> WorkerRestartResponse:
        return await self._client.restart_workers(timeout=restart_timeout)

    async def create_plugin_repo(
        self,
        source: str,
        *,
        repo_id: str | None = None,
        enabled: bool = True,
    ) -> PluginRepoResponse:
        return await self._client.create_plugin_repo(
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
    ) -> PluginRepoResponse:
        return await self._client.update_plugin_repo(
            repo_id,
            source=source,
            enabled=enabled,
        )

    async def delete_plugin_repo(self, repo_id: str) -> DeletePluginRepoResponse:
        return await self._client.delete_plugin_repo(repo_id)

    async def sync_plugin_repo(self, repo_id: str) -> SyncPluginRepoResponse:
        return await self._client.sync_plugin_repo(repo_id)

    async def refresh_plugin_catalog(self) -> PluginCatalogRefreshResponse:
        return await self._client.refresh_plugin_catalog()

    async def set_plugin_routing(
        self,
        metric_name: str,
        queue: str,
    ) -> MetricQueueAssignmentResponse:
        return await self._client.set_plugin_routing(metric_name, queue)

    async def delete_plugin_routing(
        self,
        metric_name: str,
    ) -> DeleteMetricQueueResponse:
        return await self._client.delete_plugin_routing(metric_name)
