from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lyra.api import AsyncLyraAPIClient

if TYPE_CHECKING:
    from lyra.sdk.models import (
        AdminStatusResponse,
        CatalogSummaryResponse,
        ConfigSummaryResponse,
        HealthResponse,
        JobListResponse,
        PluginRepoListResponse,
        PluginRoutingResponse,
        QueuesResponse,
        WorkersResponse,
    )
    from lyra.tui.config import TuiConfig


class LyraTuiClient(Protocol):
    async def get_health(self) -> HealthResponse: ...

    async def get_admin_status(self) -> AdminStatusResponse: ...

    async def get_admin_config_summary(self) -> ConfigSummaryResponse: ...

    async def get_admin_catalog(self) -> CatalogSummaryResponse: ...

    async def get_admin_workers(self) -> WorkersResponse: ...

    async def get_admin_queues(self) -> QueuesResponse: ...

    async def list_admin_jobs(self) -> JobListResponse: ...

    async def list_plugin_repos(self) -> PluginRepoListResponse: ...

    async def list_plugin_routing(self) -> PluginRoutingResponse: ...


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
