"""Catalog persistence (global reference data) and loaders for the sizing/cost engines."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aether.core.catalog import DiskTier, FxRate, InstanceSpec, Price, PriceModel
from aether.db.models import CatalogDiskPrice, CatalogInstanceSpec, CatalogPrice, FxRateRow


def _dt(d: Any) -> datetime | None:
    return datetime(d.year, d.month, d.day, tzinfo=UTC) if d else None


async def upsert_specs(session: AsyncSession, specs: Iterable[InstanceSpec]) -> int:
    rows = [s.model_dump() for s in specs]
    if not rows:
        return 0
    stmt = insert(CatalogInstanceSpec).values(rows)
    cols = {c: stmt.excluded[c] for c in rows[0] if c not in ("provider", "sku")}
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["provider", "sku"], set_={**cols, "updated_at": func.now()}
        )
    )
    return len(rows)


async def upsert_prices(session: AsyncSession, prices: Iterable[Price], sync_id: UUID | None) -> int:
    rows = [
        {
            "provider": p.provider,
            "region": p.region,
            "sku": p.sku,
            "os": p.os,
            "model": p.model.value,
            "hourly_usd": p.hourly_usd,
            "effective_from": _dt(p.effective_from),
            "source": p.source,
            "sync_id": sync_id,
        }
        for p in prices
    ]
    for i in range(0, len(rows), 500):
        stmt = insert(CatalogPrice).values(rows[i : i + 500])
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["provider", "region", "sku", "os", "model"],
                set_={
                    "hourly_usd": stmt.excluded.hourly_usd,
                    "effective_from": stmt.excluded.effective_from,
                    "source": stmt.excluded.source,
                    "sync_id": stmt.excluded.sync_id,
                    "fetched_at": func.now(),
                },
            )
        )
    return len(rows)


async def upsert_disks(session: AsyncSession, tiers: Iterable[DiskTier]) -> int:
    rows = [
        {
            "provider": t.provider,
            "region": t.region,
            "disk_class": t.disk_class,
            "tier": t.tier or "",
            "size_gib": t.size_gib,
            "iops": t.iops,
            "monthly_usd": t.monthly_usd,
            "gib_month_usd": t.gib_month_usd,
            "source": t.source,
        }
        for t in tiers
    ]
    if not rows:
        return 0
    stmt = insert(CatalogDiskPrice).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["provider", "region", "disk_class", "tier"],
            set_={c: stmt.excluded[c] for c in ("size_gib", "iops", "monthly_usd", "gib_month_usd", "source")}
            | {"fetched_at": func.now()},
        )
    )
    return len(rows)


async def upsert_fx(session: AsyncSession, rates: Iterable[FxRate]) -> int:
    rows = [
        {"currency": r.currency, "per_usd": r.per_usd, "rate_date": _dt(r.rate_date), "source": r.source}
        for r in rates
    ]
    if not rows:
        return 0
    stmt = insert(FxRateRow).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["currency"],
            set_={
                "per_usd": stmt.excluded.per_usd,
                "rate_date": stmt.excluded.rate_date,
                "source": stmt.excluded.source,
                "fetched_at": func.now(),
            },
        )
    )
    return len(rows)


# --------------------------------------------------------------------------- loaders


@dataclass
class RegionCatalog:
    provider: str
    region: str
    specs: list[InstanceSpec] = field(default_factory=list)
    prices: dict[tuple[str, str, PriceModel], float] = field(
        default_factory=dict
    )  # (sku, os, model) -> hourly
    disks: list[DiskTier] = field(default_factory=list)
    prices_fetched_at: datetime | None = None
    price_sources: set[str] = field(default_factory=set)

    def hourly(self, sku: str, os: str, model: PriceModel = PriceModel.ON_DEMAND) -> float | None:
        return self.prices.get((sku, os, model))


async def load_region(session: AsyncSession, provider: str, region: str) -> RegionCatalog:
    cat = RegionCatalog(provider=provider, region=region)
    spec_rows = (
        await session.execute(select(CatalogInstanceSpec).where(CatalogInstanceSpec.provider == provider))
    ).scalars()
    cat.specs = [
        InstanceSpec(
            provider=r.provider,
            sku=r.sku,
            family=r.family,
            vcpu=r.vcpu,
            memory_mib=r.memory_mib,
            cpu_arch=r.cpu_arch,
            cpu_vendor=r.cpu_vendor,
            gpu_count=r.gpu_count,
            gpu_model=r.gpu_model,
            local_disk_gib=r.local_disk_gib,
            generation=r.generation,
            spec_source=r.spec_source,
        )
        for r in spec_rows
    ]
    price_rows = (
        (
            await session.execute(
                select(CatalogPrice).where(CatalogPrice.provider == provider, CatalogPrice.region == region)
            )
        )
        .scalars()
        .all()
    )
    for p in price_rows:
        cat.prices[(p.sku, p.os, PriceModel(p.model))] = p.hourly_usd
        cat.price_sources.add(p.source)
        if cat.prices_fetched_at is None or p.fetched_at < cat.prices_fetched_at:
            cat.prices_fetched_at = p.fetched_at  # oldest price used = the conservative freshness
    disk_rows = (
        await session.execute(
            select(CatalogDiskPrice).where(
                CatalogDiskPrice.provider == provider, CatalogDiskPrice.region == region
            )
        )
    ).scalars()
    cat.disks = [
        DiskTier(
            provider=d.provider,
            region=d.region,
            disk_class=d.disk_class,
            tier=d.tier or None,
            size_gib=d.size_gib,
            iops=d.iops,
            monthly_usd=d.monthly_usd,
            gib_month_usd=d.gib_month_usd,
            source=d.source,
        )
        for d in disk_rows
    ]
    return cat


async def load_fx(session: AsyncSession, currency: str) -> FxRate | None:
    row = await session.get(FxRateRow, currency.upper())
    if row is None:
        return None
    return FxRate(
        currency=row.currency, per_usd=row.per_usd, rate_date=row.rate_date.date(), source=row.source
    )


async def priced_regions(session: AsyncSession, provider: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(CatalogPrice.region, func.count(), func.min(CatalogPrice.fetched_at))
            .where(CatalogPrice.provider == provider)
            .group_by(CatalogPrice.region)
            .order_by(CatalogPrice.region)
        )
    ).all()
    return [{"region": r, "prices": int(c), "oldest_price_at": t} for r, c, t in rows]
