"""Provider adapter Protocol and all supporting value types.

Every cloud provider adapter (AWS, Azure, GCP, IBM) must implement the
``ProviderAdapter`` Protocol defined here. The N+M adapter design means each
provider writes exactly one adapter (N providers) and each domain package
consumes the protocol directly — no N×M matrix of integration classes.

See ADR-007 for the architectural rationale.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from core.models import (
    NormalizedBundle,
    ProviderName,
    Region,
    ResourceKind,
)
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Supporting value types
# ---------------------------------------------------------------------------


class ConnCtx(BaseModel):
    """Runtime context for an authenticated provider connection.

    The adapter retrieves actual credentials from OpenBao using the
    ``connection_id`` — they are never passed directly through this struct.
    """

    connection_id: uuid.UUID
    workspace_id: uuid.UUID
    account: str         # AWS account ID / Azure subscription / GCP project
    extra: dict[str, Any] = {}


class AdapterCapabilities(BaseModel):
    """Declares what an adapter can do."""

    can_discover: bool = True
    can_fetch_metrics: bool = False
    can_sync_catalog: bool = False
    can_check_quotas: bool = False
    can_execute_changes: bool = False
    supported_kinds: set[ResourceKind] = set()


class ConnectionTestResult(BaseModel):
    """Result of ``ProviderAdapter.test_connection``."""

    ok: bool
    warnings: list[str] = []
    error: str | None = None
    latency_ms: float | None = None


class RawResource(BaseModel):
    """A raw, provider-native resource record before normalization.

    The ``data`` field holds the provider API response (serialized to dict).
    ``kind`` is the normalized kind inferred from the API response shape.
    """

    provider: ProviderName
    kind: ResourceKind
    native_id: str
    region: str
    account: str
    data: dict[str, Any]


class Window(BaseModel):
    """Time window for metric queries."""

    start: datetime
    end: datetime
    granularity_minutes: int = 60


class MetricSeries(BaseModel):
    """A time-series of metric data for one resource."""

    native_id: str
    metric_name: str
    values: list[float]
    timestamps: list[datetime]
    unit: str | None = None


class CatalogBatch(BaseModel):
    """A batch of instance-type/pricing records from the provider catalog."""

    provider: ProviderName
    region: str
    records: list[dict[str, Any]]
    fetched_at: datetime


class QuotaNeeds(BaseModel):
    """Describes what quota the planner needs in a target region."""

    vcpu: int = 0
    memory_gib: float = 0.0
    gpu: int = 0
    public_ips: int = 0


class QuotaResult(BaseModel):
    """Quota availability result for a single dimension."""

    dimension: str
    available: int | None
    requested: int
    sufficient: bool
    message: str | None = None


# ---------------------------------------------------------------------------
# Provider Adapter Protocol
# ---------------------------------------------------------------------------


class ProviderAdapter:
    """Protocol that every cloud provider adapter must satisfy.

    Adapters are intentionally not ABC subclasses — they use structural
    subtyping (Protocol) so that test doubles and stubs need not inherit
    from this class.

    All I/O methods are async. ``discover`` returns an ``AsyncIterator``
    so that the ingestion pipeline can stream resources without buffering
    an entire account in memory.
    """

    provider: ProviderName
    capabilities: AdapterCapabilities

    async def test_connection(self, ctx: ConnCtx) -> ConnectionTestResult:
        """Test that credentials in OpenBao are valid and the API is reachable."""
        raise NotImplementedError

    async def list_regions(self, ctx: ConnCtx) -> list[Region]:
        """Return the list of regions accessible with these credentials."""
        raise NotImplementedError

    def discover(
        self, ctx: ConnCtx, region: Region, kinds: set[ResourceKind]
    ) -> AsyncIterator[RawResource]:
        """Stream raw resources from the provider API.

        Each yielded ``RawResource`` is later passed to ``normalize``.
        The iterator may yield resources of multiple kinds.
        """
        raise NotImplementedError

    def normalize(self, raw: RawResource) -> NormalizedBundle:
        """Convert a single ``RawResource`` into normalized domain models.

        This method MUST be pure (no network, no I/O). It is called
        synchronously inside the discovery pipeline.
        """
        raise NotImplementedError

    async def fetch_metrics(
        self, ctx: ConnCtx, ids: list[str], window: Window
    ) -> list[MetricSeries]:
        """Fetch utilisation metrics for the given native resource IDs."""
        raise NotImplementedError

    async def sync_catalog(self, regions: list[Region]) -> CatalogBatch:
        """Fetch the provider's instance-type / pricing catalog."""
        raise NotImplementedError

    async def check_quotas(
        self, ctx: ConnCtx, region: Region, needs: QuotaNeeds
    ) -> list[QuotaResult]:
        """Check whether the account has sufficient quota in *region*."""
        raise NotImplementedError
