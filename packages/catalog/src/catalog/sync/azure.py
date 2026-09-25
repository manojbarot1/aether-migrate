"""AETHER MIGRATE — Azure catalog sync.

Syncs Azure VM sizes, availability/restriction data, and retail prices into
the catalog tables.  Azure retail prices require no authentication — they are
public.  Instance size metadata uses azure-mgmt-compute.

Security notes:
  - Azure credentials are passed in from the caller (fetched from OpenBao).
  - Retail prices API has no auth requirement; called with plain httpx.
  - Only connection_id path is logged, never credential values.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from catalog.models import CatalogSyncResult, InstanceTypeCatalog, PriceCatalog
from catalog.versioning import current_catalog_version

log = structlog.get_logger(__name__)

# Azure Retail Prices API endpoint (no auth required)
_RETAIL_PRICES_URL = (
    "https://prices.azure.com/api/retail/prices"
    "?api-version=2023-01-01-preview"
    "&$filter=serviceName eq 'Virtual Machines' and armRegionName eq '{region}'"
)

# Map Azure OS terms to normalized os_type values
_OS_TYPE_MAP = {
    "linux": "linux",
    "windows": "windows",
}


def _cpu_arch_from_azure(sku: str) -> str:
    """Infer CPU architecture from SKU name (heuristic)."""
    sku_lower = sku.lower()
    if "psv5" in sku_lower or "pdsv5" in sku_lower or "plsv5" in sku_lower:
        return "arm64"
    return "x86_64"


def _normalize_azure_price_term(price_type: str, reservation_term: str | None) -> str | None:
    """Map Azure price type and reservation term to normalized term string.

    Returns None if this price entry should be skipped.
    """
    pt = price_type.lower()
    if "consumption" in pt or "payg" in pt:
        return "on-demand"
    if "reservation" in pt or "reserved" in pt:
        if reservation_term == "1 Year":
            return "1yr-std"
        if reservation_term == "3 Years":
            return "3yr-std"
    if "savings" in pt:
        # Savings plan — treat as convertible equivalent
        if reservation_term == "1 Year":
            return "1yr-conv"
        if reservation_term == "3 Years":
            return "3yr-conv"
    return None


class AzureCatalogSync:
    """Sync Azure VM sizes, restrictions, and retail prices into catalog tables."""

    async def sync_instance_types(
        self,
        regions: list[str],
        db: AsyncSession,  # type: ignore[name-defined]  # noqa: F821
        credential: Any | None = None,
        subscription_id: str | None = None,
    ) -> CatalogSyncResult:
        """Sync Azure VM size metadata and restriction data.

        Uses azure-mgmt-compute VirtualMachineSizesOperations and
        ResourceSkusOperations.  Restriction data from Resource SKUs is
        used to mark SKUs as unavailable (restricted=True) in a subscription.

        Parameters
        ----------
        regions:
            Azure region names (e.g. "eastus", "westeurope").
        db:
            AsyncSession to write rows into.
        credential:
            azure-identity credential object.  If None, uses
            DefaultAzureCredential (suitable for local dev with az login).
        subscription_id:
            Azure subscription ID.  If None, reads from
            AZURE_SUBSCRIPTION_ID env var.
        """
        import os

        from azure.identity import DefaultAzureCredential
        from azure.mgmt.compute import ComputeManagementClient
        from db.models import CatalogInstanceTypeRow
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        version = current_catalog_version()
        effective_from = datetime.now(UTC).replace(tzinfo=None)

        if credential is None:
            credential = DefaultAzureCredential()
        if subscription_id is None:
            subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")

        compute_client = ComputeManagementClient(credential, subscription_id)

        # Build restriction map from Resource SKUs API (subscription-level availability)
        # key: (region, sku_name) → True means restricted
        restricted_skus: dict[tuple[str, str], bool] = {}
        try:
            for sku_page in compute_client.resource_skus.list():
                if sku_page.resource_type != "virtualMachines":
                    continue
                sku_regions = [
                    loc.location.lower()
                    for loc in (sku_page.location_info or [])
                ]
                has_restrictions = bool(sku_page.restrictions)
                for r in sku_regions:
                    if r in [reg.lower() for reg in regions]:
                        restricted_skus[(r, sku_page.name)] = has_restrictions
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "azure_catalog.resource_skus_unavailable",
                error=str(exc),
            )

        total_synced = 0
        errors: list[str] = []

        for region in regions:
            log.info("azure_catalog.sync_instance_types.start", region=region)
            try:
                sizes = compute_client.virtual_machine_sizes.list(location=region)
                instances: list[InstanceTypeCatalog] = []

                for size in sizes:
                    sku_name = size.name or ""
                    memory_mib = int(size.memory_in_mb or 0)
                    is_restricted = restricted_skus.get(
                        (region.lower(), sku_name), False
                    )

                    raw_dict = {
                        "name": sku_name,
                        "numberOfCores": size.number_of_cores,
                        "memoryInMB": size.memory_in_mb,
                        "maxDataDiskCount": size.max_data_disk_count,
                        "osDiskSizeInMB": size.os_disk_size_in_mb,
                        "resourceDiskSizeInMB": size.resource_disk_size_in_mb,
                    }

                    instances.append(
                        InstanceTypeCatalog(
                            provider="azure",
                            catalog_version=version,
                            effective_from=effective_from,
                            region=region,
                            sku=sku_name,
                            vcpu=size.number_of_cores or 0,
                            memory_mib=memory_mib,
                            cpu_arch=_cpu_arch_from_azure(sku_name),
                            gpu_count=0,
                            os_support={"linux": True, "windows": True},
                            available_in_region=not is_restricted,
                            restricted=is_restricted,
                            raw=raw_dict,
                        )
                    )

                # Upsert in batches
                for i in range(0, len(instances), 500):
                    batch = instances[i : i + 500]
                    values = [
                        {
                            "id": uuid.uuid4(),
                            "provider": it.provider.value,
                            "catalog_version": it.catalog_version,
                            "effective_from": it.effective_from,
                            "region": it.region,
                            "sku": it.sku,
                            "vcpu": it.vcpu,
                            "memory_mib": it.memory_mib,
                            "cpu_arch": it.cpu_arch,
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
                            "available_in_region": stmt.excluded.available_in_region,
                            "restricted": stmt.excluded.restricted,
                            "raw": stmt.excluded.raw,
                        },
                    )
                    await db.execute(stmt)

                await db.commit()
                total_synced += len(instances)
                log.info(
                    "azure_catalog.sync_instance_types.done",
                    region=region,
                    count=len(instances),
                )

            except Exception as exc:  # noqa: BLE001
                errors.append(f"{region}: {exc}")
                log.error(
                    "azure_catalog.sync_instance_types.error",
                    region=region,
                    error=str(exc),
                )

        return CatalogSyncResult(
            provider="azure",
            region=",".join(regions),
            instance_types_synced=total_synced,
            prices_synced=0,
            errors=errors,
        )

    async def sync_prices(
        self,
        regions: list[str],
        db: AsyncSession,  # type: ignore[name-defined]  # noqa: F821
    ) -> CatalogSyncResult:
        """Sync Azure retail prices using the public Retail Prices API.

        No authentication required.  Paginates through ``nextPageLink`` until
        all pages are consumed.  Prices are stored incrementally per page to
        keep memory usage bounded.
        """
        import httpx
        from db.models import CatalogPriceRow
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        version = current_catalog_version()
        effective_from = datetime.now(UTC).replace(tzinfo=None)

        total_synced = 0
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=60.0) as client:
            for region in regions:
                log.info("azure_catalog.sync_prices.start", region=region)
                prices: list[PriceCatalog] = []
                try:
                    url: str | None = _RETAIL_PRICES_URL.format(region=region)
                    while url:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        data = resp.json()

                        for item in data.get("Items", []):
                            try:
                                parsed = self._parse_price_item(
                                    item, region, version, effective_from
                                )
                                if parsed is not None:
                                    prices.append(parsed)
                            except Exception as exc:  # noqa: BLE001
                                errors.append(f"{region}/{item.get('skuName')}: {exc}")

                        url = data.get("NextPageLink")

                    # Upsert accumulated prices for this region
                    if prices:
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
                        total_synced += len(prices)

                    log.info(
                        "azure_catalog.sync_prices.done",
                        region=region,
                        count=len(prices),
                    )

                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{region}: {exc}")
                    log.error(
                        "azure_catalog.sync_prices.error",
                        region=region,
                        error=str(exc),
                    )

        return CatalogSyncResult(
            provider="azure",
            region=",".join(regions),
            instance_types_synced=0,
            prices_synced=total_synced,
            errors=errors,
        )

    def _parse_price_item(
        self,
        item: dict[str, Any],
        region: str,
        version: str,
        effective_from: datetime,
    ) -> PriceCatalog | None:
        """Parse a single Azure Retail Prices API item.

        Returns ``None`` if the item should be skipped (e.g. non-VM, no price).
        """
        sku_name: str = item.get("armSkuName", "") or item.get("skuName", "")
        if not sku_name:
            return None

        price_usd = float(item.get("retailPrice", 0))
        if price_usd <= 0:
            return None

        # Determine OS type
        product_name: str = (item.get("productName") or "").lower()
        if "windows" in product_name:
            os_type = "windows"
        else:
            os_type = "linux"

        # Determine term
        price_type: str = item.get("type", "") or item.get("priceType", "")
        reservation_term: str | None = item.get("reservationTerm")
        term = _normalize_azure_price_term(price_type, reservation_term)
        if term is None:
            return None

        # Determine license model (BYOL vs included)
        license_model = "byol" if "ahb" in (item.get("skuName") or "").lower() else "included"

        return PriceCatalog(
            provider="azure",
            catalog_version=version,
            effective_from=effective_from,
            region=region,
            sku=sku_name,
            os_type=os_type,
            license_model=license_model,
            term=term,
            price_usd=price_usd,
            currency=item.get("currencyCode", "USD"),
            price_per="hour",
            raw=item,
        )
