"""Deterministic cost model (PROJECT_PLAN §13.3). Pure functions over catalog data.

Outputs are *estimates* from list prices. Every result carries the assumptions and
price sources used, and never claims to be a quote.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from aether.core.catalog import HOURS_PER_MONTH, DiskTier, FxRate, PriceModel
from aether.core.inventory import DiskSpec, VmSpec

PriceFn = Callable[[str, str, PriceModel], float | None]
SCENARIOS = (PriceModel.ON_DEMAND, PriceModel.RESERVED_1Y, PriceModel.RESERVED_3Y)


class CostOptions(BaseModel):
    target_disk_class: str = "premium_ssd"
    egress_usd_per_gib: float = Field(0.09, description="source egress rate for the one-time data transfer")
    dual_running_days: int = Field(14, ge=0, le=365)
    currency: str = "USD"


class DiskCost(BaseModel):
    device: str | None
    size_gib: int | None
    mapped_to: str | None
    monthly_usd: float | None
    note: str | None = None


class SideCost(BaseModel):
    """Monthly cost of one VM on one side (source or target), per pricing scenario."""

    sku: str | None
    os: str
    compute_monthly_usd: dict[str, float | None]
    disks: list[DiskCost]
    disks_monthly_usd: float | None
    total_monthly_usd: dict[str, float | None]


class OneTime(BaseModel):
    egress_gib: int
    egress_usd: float
    dual_running_usd: float | None


class VmCost(BaseModel):
    source: SideCost
    target: SideCost
    one_time: OneTime
    assumptions: list[str]


def _os(vm: VmSpec) -> str:
    return "windows" if vm.os_family == "windows" else "linux"


GP3_BASELINE_IOPS = 3000


def required_iops(disk: DiskSpec) -> int | None:
    """IOPS the workload explicitly provisioned. gp3's 3000 baseline and gp2's size-derived
    IOPS are defaults, not requirements; io1/io2 (and gp3 above baseline) are."""
    if not disk.iops:
        return None
    kind = (disk.type_class or "").lower()
    if kind in ("io1", "io2"):
        return disk.iops
    if kind == "gp3" and disk.iops > GP3_BASELINE_IOPS:
        return disk.iops
    return None


def pick_tier(
    disk: DiskSpec, tiers: Sequence[DiskTier], disk_class: str
) -> tuple[DiskTier | None, str | None]:
    """Smallest fixed tier with capacity ≥ disk size and IOPS ≥ explicitly provisioned IOPS."""
    candidates = sorted(
        (t for t in tiers if t.disk_class == disk_class and t.size_gib), key=lambda t: t.size_gib or 0
    )
    if not candidates:
        return None, f"no {disk_class} prices in catalog"
    size = disk.size_gib or 0
    iops = required_iops(disk)
    for t in candidates:
        if (t.size_gib or 0) >= size and (iops is None or (t.iops or 0) >= iops):
            return t, None
    by_size = next((t for t in candidates if (t.size_gib or 0) >= size), candidates[-1])
    return by_size, f"no {disk_class} tier meets {iops} IOPS; consider Premium SSD v2 / Ultra"


def _side(sku: str | None, os_: str, price: PriceFn, disks: list[DiskCost]) -> SideCost:
    compute: dict[str, float | None] = {}
    for model in SCENARIOS:
        hourly = price(sku, os_, model) if sku else None
        compute[model.value] = round(hourly * HOURS_PER_MONTH, 2) if hourly is not None else None
    disk_total = (
        None
        if any(d.monthly_usd is None for d in disks)
        else round(sum(d.monthly_usd or 0 for d in disks), 2)
    )
    totals = {
        m: (round(c + disk_total, 2) if c is not None and disk_total is not None else None)
        for m, c in compute.items()
    }
    return SideCost(
        sku=sku,
        os=os_,
        compute_monthly_usd=compute,
        disks=disks,
        disks_monthly_usd=disk_total,
        total_monthly_usd=totals,
    )


def estimate(
    vm: VmSpec,
    target_sku: str | None,
    target_price: PriceFn,
    target_disks: Sequence[DiskTier],
    source_price: PriceFn,
    source_disks: Sequence[DiskTier],
    options: CostOptions,
) -> VmCost:
    os_ = _os(vm)
    assumptions = [
        f"{HOURS_PER_MONTH:g} hours per month, running 24x7",
        "list prices; existing discounts (EDP, Savings Plans, EA) are not applied",
        "reserved scenarios: 1- and 3-year terms, no upfront payment (AWS standard RI; Azure reservation)",
    ]
    if vm.os_family == "unknown":
        assumptions.append("operating system unknown: priced as Linux")
    if os_ == "windows":
        assumptions.append("Windows licence included (no Azure Hybrid Benefit / BYOL applied)")

    persistent = [d for d in vm.disks if not d.ephemeral]
    if any(d.ephemeral for d in vm.disks):
        assumptions.append("instance-store (ephemeral) disks are not migrated or priced")

    # --- source (AWS EBS per GiB-month by volume type)
    src_disk_costs: list[DiskCost] = []
    ebs = {t.disk_class: t.gib_month_usd for t in source_disks if t.gib_month_usd is not None}
    for d in persistent:
        rate = ebs.get(d.type_class or "")
        src_disk_costs.append(
            DiskCost(
                device=d.device,
                size_gib=d.size_gib,
                mapped_to=d.type_class,
                monthly_usd=round(rate * (d.size_gib or 0), 2) if rate is not None else None,
                note=None if rate is not None else "source storage price unavailable",
            )
        )
    if any(required_iops(d) for d in persistent):
        assumptions.append("source provisioned IOPS/throughput above the volume baseline are not priced")

    # --- target (fixed-size managed disk tiers)
    tgt_disk_costs: list[DiskCost] = []
    for d in persistent:
        tier, note = pick_tier(d, target_disks, options.target_disk_class)
        tgt_disk_costs.append(
            DiskCost(
                device=d.device,
                size_gib=d.size_gib,
                mapped_to=tier.tier if tier else None,
                monthly_usd=tier.monthly_usd if tier else None,
                note=note,
            )
        )
    assumptions.append(
        f"target disks priced as {options.target_disk_class.replace('_', ' ')} (LRS) fixed tiers"
    )

    source = _side(vm.source_sku, os_, source_price, src_disk_costs)
    target = _side(target_sku, os_, target_price, tgt_disk_costs)

    egress_gib = sum(d.size_gib or 0 for d in persistent)
    target_od = target.total_monthly_usd.get(PriceModel.ON_DEMAND.value)
    one_time = OneTime(
        egress_gib=egress_gib,
        egress_usd=round(egress_gib * options.egress_usd_per_gib, 2),
        dual_running_usd=round(target_od * options.dual_running_days / 30, 2)
        if target_od is not None
        else None,
    )
    assumptions.append(
        f"one-time transfer: provisioned disk size x ${options.egress_usd_per_gib}/GiB source egress "
        "(upper bound; compressed or thin-provisioned data transfers less)"
    )
    assumptions.append(
        f"dual running: target billed on demand for {options.dual_running_days} days during cutover"
    )
    return VmCost(source=source, target=target, one_time=one_time, assumptions=assumptions)


def convert(amount: float | None, fx: FxRate | None) -> float | None:
    if amount is None:
        return None
    return round(amount * (fx.per_usd if fx else 1.0), 2)
