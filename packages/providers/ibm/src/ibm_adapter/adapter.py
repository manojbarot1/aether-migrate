"""IBM Cloud provider adapter stub.

Phase 2d will fill in the full implementation using ibm-platform-services
and ibm-vpc Python SDKs.

Capabilities (planned for Phase 2d):
- Discovery: VPC Virtual Server Instances, Block Storage, vNICs, VPCs, Subnets, Security Groups
- Metrics: IBM Cloud Monitoring (Sysdig) metrics API
- Catalog: IBM Cloud Global Catalog / Pricing API
- Quotas: VPC quota endpoints
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


class IBMAdapter(ProviderAdapter):
    """IBM Cloud provider adapter.

    Phase 2d implementation status: STUB — all methods raise NotImplementedError.
    """

    provider: ProviderName = ProviderName.ibm
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
        },
    )

    async def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        """Phase 2d: validate IBM Cloud API key via IAM token exchange."""
        raise NotImplementedError("IBMAdapter.test_connection: Phase 2d")

    async def list_regions(self, ctx: ConnCtx) -> list[Region]:
        """Phase 2d: list VPC regions."""
        raise NotImplementedError("IBMAdapter.list_regions: Phase 2d")

    def discover(
        self, ctx: ConnCtx, region: Region, kinds: set[ResourceKind]
    ) -> AsyncIterator[RawResource]:
        """Phase 2d: paginate IBM VPC SDK list_instances, list_volumes etc."""
        raise NotImplementedError("IBMAdapter.discover: Phase 2d")

    def normalize(self, raw: RawResource) -> NormalizedBundle:
        """Phase 2d: map IBM VPC instance resource to normalized models."""
        raise NotImplementedError("IBMAdapter.normalize: Phase 2d")

    async def fetch_metrics(
        self, ctx: ConnCtx, ids: list[str], window: Window
    ) -> list[MetricSeries]:
        """Phase 2d: query IBM Cloud Monitoring metrics."""
        raise NotImplementedError("IBMAdapter.fetch_metrics: Phase 2d")

    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch:
        """Phase 2d: query IBM Global Catalog pricing."""
        raise NotImplementedError("IBMAdapter.sync_catalog: Phase 2d")

    async def check_quotas(
        self, ctx: ConnCtx, region: Region, needs: QuotaNeeds
    ) -> list[QuotaResult]:
        """Phase 2d: query IBM VPC quota endpoints."""
        raise NotImplementedError("IBMAdapter.check_quotas: Phase 2d")
