"""AETHER MIGRATE — catalog domain models (Pydantic)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.models import ProviderName
from pydantic import BaseModel


class InstanceTypeCatalog(BaseModel):
    """Parsed, validated instance type data from a provider catalog."""

    provider: ProviderName
    catalog_version: str          # e.g. "2025-01"
    effective_from: datetime
    region: str
    sku: str                       # e.g. "m5.xlarge", "Standard_D4s_v5"
    vcpu: int
    memory_mib: int
    cpu_arch: str                  # "x86_64" | "arm64"
    cpu_vendor: str | None = None
    gpu_model: str | None = None
    gpu_count: int = 0
    local_nvme_gib: int = 0
    network_bandwidth_gbps: float | None = None
    os_support: dict[str, Any] = {}
    available_in_region: bool = True
    restricted: bool = False
    raw: dict[str, Any] | None = None


class PriceCatalog(BaseModel):
    """Single price point for an instance type."""

    provider: ProviderName
    catalog_version: str
    effective_from: datetime
    region: str
    sku: str
    os_type: str                   # "linux", "windows", "windows-sql-std", etc.
    license_model: str             # "included", "byol"
    term: str                      # "on-demand", "1yr-std", "1yr-conv", "3yr-std", "3yr-conv"
    price_usd: float
    currency: str = "USD"
    price_per: str = "hour"
    raw: dict[str, Any] | None = None


class DiskPriceCatalog(BaseModel):
    """Price data for a block storage type."""

    provider: ProviderName
    catalog_version: str
    effective_from: datetime
    region: str
    disk_type: str
    price_per_gib_month: float | None = None
    price_per_iops_month: float | None = None
    price_per_mbps_month: float | None = None
    max_size_gib: int | None = None
    max_iops: int | None = None
    max_throughput_mbps: int | None = None
    raw: dict[str, Any] | None = None


class CatalogSyncResult(BaseModel):
    """Summary of a completed catalog sync operation."""

    provider: str
    region: str
    instance_types_synced: int
    prices_synced: int
    errors: list[str] = []
