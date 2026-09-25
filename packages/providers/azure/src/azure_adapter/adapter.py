"""Azure provider adapter stub.

Phase 2b will fill in the full implementation using azure-mgmt-compute,
azure-mgmt-network, and azure-monitor.

Capabilities (planned for Phase 2b):
- Discovery: VMs, Managed Disks, NICs, VNets, subnets, NSGs, Load Balancers
- Metrics: Azure Monitor metrics API
- Catalog: Azure Retail Prices API
- Quotas: Azure Quota API
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from core.models import (
    NormalizedBundle,
    ProviderName,
    Region,
    ResourceKind,
)
from providers_base.adapter import (
    AdapterCapabilities,
    CatalogBatch,
    ConnCtx,
    ConnectionTestResult,
    MetricSeries,
    ProviderAdapter,
    QuotaNeeds,
    QuotaResult,
    RawResource,
    Window,
)


class AzureAdapter(ProviderAdapter):
    """Azure cloud provider adapter.

    Phase 2b implementation status: STUB — all methods raise NotImplementedError.
    """

    provider: ProviderName = ProviderName.azure
    capabilities: AdapterCapabilities = AdapterCapabilities(
        can_discover=True,
        can_fetch_metrics=True,
        can_sync_catalog=True,
        can_check_quotas=True,
        can_execute_changes=False,
        supported_kinds={
            ResourceKind.vm,
            ResourceKind.disk,
            ResourceKind.nic,
            ResourceKind.network,
            ResourceKind.subnet,
            ResourceKind.security_group,
            ResourceKind.load_balancer,
        },
    )

    async def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        """Phase 2b: validate service principal via azure-identity."""
        raise NotImplementedError("AzureAdapter.test_connection: Phase 2b")

    async def list_regions(self, ctx: ConnCtx) -> list[Region]:
        """Phase 2b: list subscription locations."""
        raise NotImplementedError("AzureAdapter.list_regions: Phase 2b")

    def discover(
        self, ctx: ConnCtx, region: Region, kinds: set[ResourceKind]
    ) -> AsyncIterator[RawResource]:
        """Phase 2b: paginate azure-mgmt-compute and azure-mgmt-network APIs."""
        raise NotImplementedError("AzureAdapter.discover: Phase 2b")

    def normalize(self, raw: RawResource) -> NormalizedBundle:
        """Phase 2b: map Azure VM/disk/NIC response to normalized models."""
        raise NotImplementedError("AzureAdapter.normalize: Phase 2b")

    async def fetch_metrics(
        self, ctx: ConnCtx, ids: list[str], window: Window
    ) -> list[MetricSeries]:
        """Phase 2b: query Azure Monitor metrics."""
        raise NotImplementedError("AzureAdapter.fetch_metrics: Phase 2b")

    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch:
        """Phase 2b: query Azure Retail Prices API."""
        raise NotImplementedError("AzureAdapter.sync_catalog: Phase 2b")

    async def check_quotas(
        self, ctx: ConnCtx, region: Region, needs: QuotaNeeds
    ) -> list[QuotaResult]:
        """Phase 2b: query Azure Quota API."""
        raise NotImplementedError("AzureAdapter.check_quotas: Phase 2b")
