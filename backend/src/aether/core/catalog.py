"""Catalog domain types: instance specs, prices, disk tiers, FX. Pure data."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

HOURS_PER_MONTH = 730.0  # the convention used by AWS and Azure pricing calculators


class PriceModel(StrEnum):
    ON_DEMAND = "on_demand"
    RESERVED_1Y = "reserved_1y"
    RESERVED_3Y = "reserved_3y"


class InstanceSpec(BaseModel):
    provider: str
    sku: str
    family: str
    vcpu: int
    memory_mib: int
    cpu_arch: Literal["x86_64", "arm64"]
    cpu_vendor: str | None = None
    gpu_count: int = 0
    gpu_model: str | None = None
    local_disk_gib: int = 0
    generation: int = 0
    spec_source: str  # "curated" | "api"


class Price(BaseModel):
    provider: str
    region: str
    sku: str
    os: Literal["linux", "windows"]
    model: PriceModel
    hourly_usd: float
    effective_from: date | None = None
    source: str  # e.g. "azure-retail-prices", "aws-price-list"


class DiskTier(BaseModel):
    """A fixed-size managed-disk tier (Azure P/E tiers) or a per-GiB class (AWS EBS)."""

    provider: str
    region: str
    disk_class: str  # "premium_ssd" | "standard_ssd" | "gp3" | ...
    tier: str | None = None  # e.g. "P10"
    size_gib: int | None = None  # tier capacity; None for per-GiB classes
    iops: int | None = None
    monthly_usd: float | None = None  # per disk (tiers)
    gib_month_usd: float | None = None  # per GiB (per-GiB classes)
    source: str


class FxRate(BaseModel):
    currency: str
    per_usd: float = Field(description="units of `currency` per 1 USD")
    rate_date: date
    source: str = "ECB euro foreign exchange reference rates"
