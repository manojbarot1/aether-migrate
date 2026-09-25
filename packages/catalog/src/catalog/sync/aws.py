"""AETHER MIGRATE — AWS catalog sync.

Syncs EC2 instance type metadata and pricing from AWS APIs into the catalog
tables.  The pricing bulk JSON file can be very large; we stream and parse it
incrementally rather than loading it entirely into memory.

Security notes:
  - AWS credentials are passed in from the caller (fetched from OpenBao).
  - Only the connection_id path is logged, never credential values.
  - boto3 clients are constructed per-call so credentials are never cached
    at module level.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from catalog.models import CatalogSyncResult, InstanceTypeCatalog, PriceCatalog
from catalog.versioning import current_catalog_version

log = structlog.get_logger(__name__)

# AWS Pricing API is only available in us-east-1
_PRICING_REGION = "us-east-1"

# Map AWS region names to the display names used in the bulk price file
_REGION_DISPLAY_MAP: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-central-1": "Europe (Frankfurt)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-south-1": "Asia Pacific (Mumbai)",
}


def _arch_from_aws(arch_list: list[str]) -> str:
    """Normalize AWS cpu architectures to x86_64 or arm64."""
    for a in arch_list:
        if "arm" in a.lower() or "graviton" in a.lower():
            return "arm64"
    return "x86_64"


def _parse_instance_type(raw: dict[str, Any], region: str, version: str) -> InstanceTypeCatalog:
    """Convert an EC2 describe_instance_types entry into InstanceTypeCatalog."""
    vcpu_info = raw.get("VCpuInfo", {})
    mem_info = raw.get("MemoryInfo", {})
    net_info = raw.get("NetworkInfo", {})
    gpu_info = raw.get("GpuInfo", {})
    nvme_info = raw.get("InstanceStorageInfo", {})

    gpu_count = 0
    gpu_model: str | None = None
    if gpu_info and gpu_info.get("Gpus"):
        for g in gpu_info["Gpus"]:
            gpu_count += g.get("Count", 0)
            gpu_model = g.get("Name") or gpu_model

    nvme_gib = 0
    if nvme_info and nvme_info.get("TotalSizeInGB"):
        nvme_gib = int(nvme_info["TotalSizeInGB"])

    net_bw: float | None = None
    if net_info.get("NetworkCards"):
        # Sum baseline bandwidth across cards
        total = sum(
            c.get("BaselineBandwidthInGbps", 0.0) for c in net_info["NetworkCards"]
        )
        if total > 0:
            net_bw = total

    archs = raw.get("ProcessorInfo", {}).get("SupportedArchitectures", [])
    os_support = {"linux": True, "windows": True}  # EC2 defaults

    return InstanceTypeCatalog(
        provider="aws",
        catalog_version=version,
        effective_from=datetime.now(UTC).replace(tzinfo=None),
        region=region,
        sku=raw.get("InstanceType", ""),
        vcpu=vcpu_info.get("DefaultVCpus", 0),
        memory_mib=mem_info.get("SizeInMiB", 0),
        cpu_arch=_arch_from_aws(archs),
        cpu_vendor=raw.get("ProcessorInfo", {}).get("SustainedClockSpeedInGhz") and "Intel"
            or None,
        gpu_model=gpu_model,
        gpu_count=gpu_count,
        local_nvme_gib=nvme_gib,
        network_bandwidth_gbps=net_bw,
        os_support=os_support,
        available_in_region=True,
        restricted=False,
        raw=raw,
    )


class AWSCatalogSync:
    """Sync AWS EC2 instance types and pricing into catalog tables."""

    async def sync_instance_types(
        self,
        regions: list[str],
        db: AsyncSession,  # type: ignore[name-defined]  # noqa: F821
        boto3_session: Any | None = None,
    ) -> CatalogSyncResult:
        """Sync instance type metadata for the given regions.

        Parameters
        ----------
        regions:
            List of AWS region names to sync.
        db:
            AsyncSession to write rows into.
        boto3_session:
            Optional pre-configured boto3 Session (with credentials).  If
            None, the default session is used (suitable for tests and local
            development with instance profiles).
        """
        import boto3
        from db.models import CatalogInstanceTypeRow
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        version = current_catalog_version()
        effective_from = datetime.now(UTC).replace(tzinfo=None)

        total_synced = 0
        errors: list[str] = []

        session = boto3_session or boto3.Session()

        for region in regions:
            log.info("aws_catalog.sync_instance_types.start", region=region)
            try:
                ec2 = session.client("ec2", region_name=region)
                paginator = ec2.get_paginator("describe_instance_types")
                instances: list[InstanceTypeCatalog] = []

                for page in paginator.paginate():
                    for raw_it in page.get("InstanceTypes", []):
                        try:
                            it = _parse_instance_type(raw_it, region, version)
                            instances.append(it)
                        except Exception as exc:
                            errors.append(f"{region}/{raw_it.get('InstanceType')}: {exc}")

                # Upsert in batches of 500
                for i in range(0, len(instances), 500):
                    batch = instances[i : i + 500]
                    values = [
                        {
                            "id": uuid.uuid4(),
                            "provider": it.provider.value,
                            "catalog_version": it.catalog_version,
                            "effective_from": effective_from,
                            "region": it.region,
                            "sku": it.sku,
                            "vcpu": it.vcpu,
                            "memory_mib": it.memory_mib,
                            "cpu_arch": it.cpu_arch,
                            "cpu_vendor": it.cpu_vendor,
                            "gpu_model": it.gpu_model,
                            "gpu_count": it.gpu_count,
                            "local_nvme_gib": it.local_nvme_gib,
                            "network_bandwidth_gbps": it.network_bandwidth_gbps,
                            "os_support": it.os_support,
                            "available_in_region": it.available_in_region,
                            "restricted": it.restricted,
                            "raw": it.raw,
                        }
                        for it in batch
                    ]
                    stmt = pg_insert(CatalogInstanceTypeRow).values(values)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_catalog_instance_types",
                        set_={
                            "vcpu": stmt.excluded.vcpu,
                            "memory_mib": stmt.excluded.memory_mib,
                            "cpu_arch": stmt.excluded.cpu_arch,
                            "gpu_count": stmt.excluded.gpu_count,
                            "network_bandwidth_gbps": stmt.excluded.network_bandwidth_gbps,
                            "os_support": stmt.excluded.os_support,
                            "raw": stmt.excluded.raw,
                        },
                    )
                    await db.execute(stmt)

                await db.commit()
                total_synced += len(instances)
                log.info("aws_catalog.sync_instance_types.done", region=region, count=len(instances))

            except Exception as exc:  # noqa: BLE001
                errors.append(f"{region}: {exc}")
                log.error("aws_catalog.sync_instance_types.error", region=region, error=str(exc))

        return CatalogSyncResult(
            provider="aws",
            region=",".join(regions),
            instance_types_synced=total_synced,
            prices_synced=0,
            errors=errors,
        )

    async def sync_prices(
        self,
        regions: list[str],
        db: AsyncSession,  # type: ignore[name-defined]  # noqa: F821
        boto3_session: Any | None = None,
    ) -> CatalogSyncResult:
        """Sync EC2 on-demand and reserved pricing for Linux instances.

        Uses the AWS Pricing List Query API (endpoint: us-east-1).  The bulk
        file can be very large; we stream and parse incrementally.

        Only the pricing API is called; no per-region API call needed — all
        regional prices are in the single bulk file.
        """
        import boto3
        from db.models import CatalogPriceRow
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        version = current_catalog_version()
        effective_from = datetime.now(UTC).replace(tzinfo=None)
        allowed_display_names = {
            _REGION_DISPLAY_MAP.get(r) for r in regions if r in _REGION_DISPLAY_MAP
        }

        session = boto3_session or boto3.Session()
        pricing_client = session.client("pricing", region_name=_PRICING_REGION)

        prices: list[PriceCatalog] = []
        errors: list[str] = []

        try:
            paginator = pricing_client.get_paginator("get_products")
            for page in paginator.paginate(
                ServiceCode="AmazonEC2",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                    {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                    {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                ],
            ):
                for price_str in page.get("PriceList", []):
                    try:
                        item = json.loads(price_str)
                        attrs = item.get("product", {}).get("attributes", {})
                        loc = attrs.get("location", "")

                        if allowed_display_names and loc not in allowed_display_names:
                            continue

                        sku = attrs.get("instanceType", "")
                        if not sku:
                            continue

                        # Reverse-map display name → region code
                        region_code = next(
                            (k for k, v in _REGION_DISPLAY_MAP.items() if v == loc),
                            loc,
                        )

                        terms = item.get("terms", {})

                        # On-demand pricing
                        for _od_key, od_val in terms.get("OnDemand", {}).items():
                            for _pd_key, pd_val in od_val.get("priceDimensions", {}).items():
                                price_usd_str = pd_val.get("pricePerUnit", {}).get("USD", "0")
                                try:
                                    price_usd = float(price_usd_str)
                                except ValueError:
                                    continue
                                if price_usd == 0:
                                    continue
                                prices.append(
                                    PriceCatalog(
                                        provider="aws",
                                        catalog_version=version,
                                        effective_from=effective_from,
                                        region=region_code,
                                        sku=sku,
                                        os_type="linux",
                                        license_model="included",
                                        term="on-demand",
                                        price_usd=price_usd,
                                        raw=item,
                                    )
                                )

                        # Reserved pricing (1yr/3yr, Standard/Convertible)
                        _ri_term_map = {
                            ("1yr", "Standard"): "1yr-std",
                            ("1yr", "Convertible"): "1yr-conv",
                            ("3yr", "Standard"): "3yr-std",
                            ("3yr", "Convertible"): "3yr-conv",
                        }
                        for _ri_key, ri_val in terms.get("Reserved", {}).items():
                            ri_attrs = ri_val.get("termAttributes", {})
                            duration = ri_attrs.get("LeaseContractLength", "")
                            offering = ri_attrs.get("OfferingClass", "")
                            payment = ri_attrs.get("PurchaseOption", "")
                            term_key = (duration, offering)
                            if term_key not in _ri_term_map:
                                continue
                            if payment not in ("No Upfront", "Partial Upfront", "All Upfront"):
                                continue
                            for _pd_key, pd_val in ri_val.get("priceDimensions", {}).items():
                                desc = pd_val.get("description", "").lower()
                                if "upfront" in desc:
                                    continue  # skip one-time upfront fee; use hourly rate
                                price_usd_str = pd_val.get("pricePerUnit", {}).get("USD", "0")
                                try:
                                    price_usd = float(price_usd_str)
                                except ValueError:
                                    continue
                                if price_usd == 0:
                                    continue
                                prices.append(
                                    PriceCatalog(
                                        provider="aws",
                                        catalog_version=version,
                                        effective_from=effective_from,
                                        region=region_code,
                                        sku=sku,
                                        os_type="linux",
                                        license_model="included",
                                        term=_ri_term_map[term_key],
                                        price_usd=price_usd,
                                        raw=None,  # save space — raw RI data is large
                                    )
                                )

                    except Exception as exc:  # noqa: BLE001
                        errors.append(str(exc))

                # Flush each page incrementally to avoid memory build-up
                if prices:
                    await self._upsert_prices(prices, db, pg_insert, CatalogPriceRow)
                    prices = []

        except Exception as exc:  # noqa: BLE001
            errors.append(f"pricing_api: {exc}")
            log.error("aws_catalog.sync_prices.error", error=str(exc))

        if prices:
            await self._upsert_prices(prices, db, pg_insert, CatalogPriceRow)

        return CatalogSyncResult(
            provider="aws",
            region=",".join(regions),
            instance_types_synced=0,
            prices_synced=0,  # count is approximate; actual rows upserted in batches
            errors=errors,
        )

    @staticmethod
    async def _upsert_prices(
        prices: list[PriceCatalog],
        db: Any,
        pg_insert: Any,
        CatalogPriceRow: Any,
    ) -> None:
        for i in range(0, len(prices), 500):
            batch = prices[i : i + 500]
            values = [
                {
                    "id": uuid.uuid4(),
                    "provider": p.provider.value,
                    "catalog_version": p.catalog_version,
                    "effective_from": p.effective_from,
                    "region": p.region,
                    "sku": p.sku,
                    "os_type": p.os_type,
                    "license_model": p.license_model,
                    "term": p.term,
                    "price_usd": str(p.price_usd),
                    "currency": p.currency,
                    "price_per": p.price_per,
                    "raw": p.raw,
                }
                for p in batch
            ]
            stmt = pg_insert(CatalogPriceRow).values(values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_catalog_prices",
                set_={"price_usd": stmt.excluded.price_usd},
            )
            await db.execute(stmt)
        await db.commit()
