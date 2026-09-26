"""Azure target catalog: VM size specs (curated) and prices (Azure Retail Prices API).

Specs are curated from Azure's published size tables because the Retail Prices API does
not carry vCPU/memory, and the authoritative Resource SKUs API needs a subscription.
When an Azure target connection exists, a Resource SKUs sync supersedes these rows
(``spec_source = "api"``). Every recommendation shows which source it used.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import date
from typing import Any

import httpx

from aether.core.catalog import HOURS_PER_MONTH, DiskTier, InstanceSpec, Price, PriceModel

RETAIL_API = "https://prices.azure.com/api/retail/prices"
RETAIL_API_VERSION = "2023-01-01-preview"
SOURCE = "azure-retail-prices"

# family -> (sku template, vendor, arch, generation, [(vcpu, memory_gib, temp_disk_gib)])
_FAMILIES: dict[str, tuple[str, str, str, int, list[tuple[int, int, int]]]] = {
    "Dsv5": (
        "Standard_D{n}s_v5",
        "Intel",
        "x86_64",
        5,
        [
            (2, 8, 0),
            (4, 16, 0),
            (8, 32, 0),
            (16, 64, 0),
            (32, 128, 0),
            (48, 192, 0),
            (64, 256, 0),
            (96, 384, 0),
        ],
    ),
    "Dasv5": (
        "Standard_D{n}as_v5",
        "AMD",
        "x86_64",
        5,
        [
            (2, 8, 0),
            (4, 16, 0),
            (8, 32, 0),
            (16, 64, 0),
            (32, 128, 0),
            (48, 192, 0),
            (64, 256, 0),
            (96, 384, 0),
        ],
    ),
    "Ddsv5": (
        "Standard_D{n}ds_v5",
        "Intel",
        "x86_64",
        5,
        [
            (2, 8, 75),
            (4, 16, 150),
            (8, 32, 300),
            (16, 64, 600),
            (32, 128, 1200),
            (48, 192, 1800),
            (64, 256, 2400),
            (96, 384, 3600),
        ],
    ),
    "Esv5": (
        "Standard_E{n}s_v5",
        "Intel",
        "x86_64",
        5,
        [
            (2, 16, 0),
            (4, 32, 0),
            (8, 64, 0),
            (16, 128, 0),
            (20, 160, 0),
            (32, 256, 0),
            (48, 384, 0),
            (64, 512, 0),
            (96, 672, 0),
        ],
    ),
    "Easv5": (
        "Standard_E{n}as_v5",
        "AMD",
        "x86_64",
        5,
        [
            (2, 16, 0),
            (4, 32, 0),
            (8, 64, 0),
            (16, 128, 0),
            (20, 160, 0),
            (32, 256, 0),
            (48, 384, 0),
            (64, 512, 0),
            (96, 672, 0),
        ],
    ),
    "Edsv5": (
        "Standard_E{n}ds_v5",
        "Intel",
        "x86_64",
        5,
        [
            (2, 16, 75),
            (4, 32, 150),
            (8, 64, 300),
            (16, 128, 600),
            (20, 160, 750),
            (32, 256, 1200),
            (48, 384, 1800),
            (64, 512, 2400),
            (96, 672, 3600),
        ],
    ),
    "Fsv2": (
        "Standard_F{n}s_v2",
        "Intel",
        "x86_64",
        2,
        [
            (2, 4, 16),
            (4, 8, 32),
            (8, 16, 64),
            (16, 32, 128),
            (32, 64, 256),
            (48, 96, 384),
            (64, 128, 512),
            (72, 144, 576),
        ],
    ),
    "Dpsv5": (
        "Standard_D{n}ps_v5",
        "Ampere",
        "arm64",
        5,
        [(2, 8, 0), (4, 16, 0), (8, 32, 0), (16, 64, 0), (32, 128, 0), (48, 192, 0), (64, 208, 0)],
    ),
    "Epsv5": (
        "Standard_E{n}ps_v5",
        "Ampere",
        "arm64",
        5,
        [(2, 16, 0), (4, 32, 0), (8, 64, 0), (16, 128, 0), (20, 160, 0), (32, 208, 0)],
    ),
}


def curated_specs() -> list[InstanceSpec]:
    specs: list[InstanceSpec] = []
    for family, (tpl, vendor, arch, gen, sizes) in _FAMILIES.items():
        for vcpu, mem_gib, temp in sizes:
            specs.append(
                InstanceSpec(
                    provider="azure",
                    sku=tpl.format(n=vcpu),
                    family=family,
                    vcpu=vcpu,
                    memory_mib=mem_gib * 1024,
                    cpu_arch=arch,
                    cpu_vendor=vendor,
                    local_disk_gib=temp,
                    generation=gen,
                    spec_source="curated",
                )
            )
    return specs


# Premium SSD / Standard SSD fixed tiers: (tier suffix, capacity GiB, premium IOPS, standard IOPS)
DISK_TIERS: list[tuple[str, int, int, int]] = [
    ("4", 32, 120, 500),
    ("6", 64, 240, 500),
    ("10", 128, 500, 500),
    ("15", 256, 1100, 500),
    ("20", 512, 2300, 500),
    ("30", 1024, 5000, 500),
    ("40", 2048, 7500, 500),
    ("50", 4096, 7500, 500),
    ("60", 8192, 16000, 2000),
    ("70", 16384, 18000, 4000),
    ("80", 32767, 20000, 6000),
]
_DISK_PRODUCTS = {
    "Premium SSD Managed Disks": ("premium_ssd", "P"),
    "Standard SSD Managed Disks": ("standard_ssd", "E"),
}


def _eff(item: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(item.get("effectiveStartDate", ""))[:10])
    except ValueError:
        return None


def parse_vm_prices(items: Iterable[dict[str, Any]], region: str, skus: set[str]) -> list[Price]:
    """Linux/Windows on-demand and 1y/3y reservation hourly prices for ``skus``.

    Retail API quirks handled here:
    * reservation ``unitPrice`` is the total for the term (despite "1 Hour" units);
    * reservations cover compute only: the Windows reserved price is the Linux reserved
      price plus the Windows licence uplift (Windows minus Linux on-demand);
    * Spot, Low Priority and Dev/Test meters are excluded.
    """
    od: dict[tuple[str, str], tuple[float, date | None]] = {}
    reserved: dict[tuple[str, PriceModel], tuple[float, date | None]] = {}
    for it in items:
        sku = it.get("armSkuName")
        if sku not in skus or it.get("armRegionName") != region or it.get("currencyCode") != "USD":
            continue
        name = f"{it.get('skuName', '')} {it.get('meterName', '')}"
        if "Spot" in name or "Low Priority" in name:
            continue
        kind = it.get("type")
        if kind == "Consumption":
            os_ = "windows" if "Windows" in str(it.get("productName", "")) else "linux"
            od[(sku, os_)] = (float(it["unitPrice"]), _eff(it))
        elif kind == "Reservation":
            term = it.get("reservationTerm")
            years = {"1 Year": 1, "3 Years": 3}.get(str(term))
            if years:
                model = PriceModel.RESERVED_1Y if years == 1 else PriceModel.RESERVED_3Y
                reserved[(sku, model)] = (float(it["unitPrice"]) / (years * 8760), _eff(it))

    prices: list[Price] = []
    for (sku, os_), (hourly, eff) in od.items():
        prices.append(
            Price(
                provider="azure",
                region=region,
                sku=sku,
                os=os_,
                model=PriceModel.ON_DEMAND,
                hourly_usd=hourly,
                effective_from=eff,
                source=SOURCE,
            )
        )
    for (sku, model), (hourly, eff) in reserved.items():
        prices.append(
            Price(
                provider="azure",
                region=region,
                sku=sku,
                os="linux",
                model=model,
                hourly_usd=hourly,
                effective_from=eff,
                source=SOURCE,
            )
        )
        lin, win = od.get((sku, "linux")), od.get((sku, "windows"))
        if lin and win:
            prices.append(
                Price(
                    provider="azure",
                    region=region,
                    sku=sku,
                    os="windows",
                    model=model,
                    hourly_usd=hourly + (win[0] - lin[0]),
                    effective_from=eff,
                    source=f"{SOURCE} (reservation + Windows licence uplift)",
                )
            )
    return prices


def parse_disk_prices(items: Iterable[dict[str, Any]], region: str) -> list[DiskTier]:
    tiers = {suffix: (cap, p_iops, s_iops) for suffix, cap, p_iops, s_iops in DISK_TIERS}
    out: list[DiskTier] = []
    for it in items:
        product = _DISK_PRODUCTS.get(str(it.get("productName")))
        if not product or it.get("armRegionName") != region or it.get("type") != "Consumption":
            continue
        disk_class, letter = product
        meter = str(it.get("meterName", ""))  # e.g. "P10 LRS Disk"
        parts = meter.split()
        if len(parts) != 3 or parts[1] != "LRS" or parts[2] != "Disk" or not parts[0].startswith(letter):
            continue
        suffix = parts[0][1:]
        if suffix not in tiers:
            continue
        cap, p_iops, s_iops = tiers[suffix]
        out.append(
            DiskTier(
                provider="azure",
                region=region,
                disk_class=disk_class,
                tier=parts[0],
                size_gib=cap,
                iops=p_iops if letter == "P" else s_iops,
                monthly_usd=float(it["unitPrice"]),
                source=SOURCE,
            )
        )
    return out


async def retail_items(client: httpx.AsyncClient, odata_filter: str) -> AsyncIterator[dict[str, Any]]:
    url: str | None = RETAIL_API
    params: dict[str, str] | None = {"api-version": RETAIL_API_VERSION, "$filter": odata_filter}
    while url:
        resp = await client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for item in body.get("Items") or []:
            yield item
        url, params = body.get("NextPageLink"), None


async def fetch_region(client: httpx.AsyncClient, region: str) -> tuple[list[Price], list[DiskTier]]:
    skus = sorted({s.sku for s in curated_specs()})
    vm_items: list[dict[str, Any]] = []
    for i in range(0, len(skus), 15):  # server-side SKU filter keeps this to a handful of pages
        chunk = " or ".join(f"armSkuName eq '{sku}'" for sku in skus[i : i + 15])
        vm_items += [
            item
            async for item in retail_items(
                client,
                f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and ({chunk})",
            )
        ]
    disk_items = [
        i
        async for i in retail_items(
            client,
            f"serviceName eq 'Storage' and armRegionName eq '{region}' and priceType eq 'Consumption' "
            "and (productName eq 'Premium SSD Managed Disks' or productName eq 'Standard SSD Managed Disks')",
        )
    ]
    return parse_vm_prices(vm_items, region, set(skus)), parse_disk_prices(disk_items, region)


def monthly(hourly: float) -> float:
    return hourly * HOURS_PER_MONTH
