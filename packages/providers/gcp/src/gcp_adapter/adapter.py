"""GCP provider adapter stub.

Phase 2c will fill in the full implementation using google-cloud-compute
and google-cloud-monitoring.

Capabilities (planned for Phase 2c):
- Discovery: Compute Instances, Persistent Disks, NICs, VPC Networks, Subnets, Firewall Rules
- Metrics: Cloud Monitoring (Stackdriver) API
- Catalog: Cloud Billing Catalog API
- Quotas: Service Usage API quotas
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


class GCPAdapter(ProviderAdapter):
    """GCP cloud provider adapter.

    Phase 2c implementation status: STUB — all methods raise NotImplementedError.
    """

    provider: ProviderName = ProviderName.gcp
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
        """Phase 2c: validate service account credentials."""
        raise NotImplementedError("GCPAdapter.test_connection: Phase 2c")

    async def list_regions(self, ctx: ConnCtx) -> list[Region]:
        """Phase 2c: list GCP regions via compute.regions.list."""
        raise NotImplementedError("GCPAdapter.list_regions: Phase 2c")

    def discover(
        self, ctx: ConnCtx, region: Region, kinds: set[ResourceKind]
    ) -> AsyncIterator[RawResource]:
        """Phase 2c: paginate GCP Compute Engine aggregatedList APIs."""
        raise NotImplementedError("GCPAdapter.discover: Phase 2c")

    def normalize(self, raw: RawResource) -> NormalizedBundle:
        """Phase 2c: map GCP Compute Instance resource to normalized models."""
        raise NotImplementedError("GCPAdapter.normalize: Phase 2c")

    async def fetch_metrics(
        self, ctx: ConnCtx, ids: list[str], window: Window
    ) -> list[MetricSeries]:
        """Phase 2c: query Cloud Monitoring timeSeries.list."""
        raise NotImplementedError("GCPAdapter.fetch_metrics: Phase 2c")

    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch:
        """Phase 2c: query Cloud Billing Catalog."""
        raise NotImplementedError("GCPAdapter.sync_catalog: Phase 2c")

    async def check_quotas(
        self, ctx: ConnCtx, region: Region, needs: QuotaNeeds
    ) -> list[QuotaResult]:
        """Phase 2c: query GCP Service Usage quota API."""
        raise NotImplementedError("GCPAdapter.check_quotas: Phase 2c")
