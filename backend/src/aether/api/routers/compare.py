"""Catalog status/sync and target comparison (sizing + cost) for discovered VMs."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from temporalio.client import Client

from aether.api.deps import (
    PrincipalDep,
    SessionDep,
    SettingsDep,
    WorkspaceContext,
    get_temporal,
    request_id,
    require_role,
)
from aether.audit.writer import AuditRecord, record
from aether.catalog.store import load_fx, load_region, priced_regions
from aether.core.catalog import PriceModel
from aether.core.enums import Role
from aether.core.errors import NotFoundError, ValidationFailedError
from aether.core.inventory import ResourceType, VmSpec
from aether.cost.engine import CostOptions, VmCost, estimate
from aether.db.models import CatalogSync, Resource
from aether.sizing.engine import SizingResult, Strategy, recommend
from aether.workflows.catalog import CatalogSyncInput, SyncCatalogWorkflow

router = APIRouter(prefix="/api/v1", tags=["cost"])
Analyst = Annotated[WorkspaceContext, Depends(require_role(Role.ANALYST))]
Temporal = Annotated[Client, Depends(get_temporal)]
RequestId = Annotated[str | None, Depends(request_id)]

STALE_AFTER = timedelta(hours=48)


@router.get("/catalog/status")
async def catalog_status(_: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    last = (
        (await session.execute(select(CatalogSync).order_by(CatalogSync.started_at.desc()).limit(5)))
        .scalars()
        .all()
    )
    return {
        "azure": await priced_regions(session, "azure"),
        "aws": await priced_regions(session, "aws"),
        "syncs": [
            {
                "id": str(s.id),
                "provider": s.provider,
                "status": s.status,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "stats": s.stats,
            }
            for s in last
        ],
    }


@router.post("/catalog/sync", status_code=status.HTTP_202_ACCEPTED)
async def catalog_sync(
    principal: PrincipalDep, session: SessionDep, temporal: Temporal, settings: SettingsDep, rid: RequestId
) -> dict[str, str]:
    principal.require_platform_admin()
    await record(
        session,
        AuditRecord(
            actor=principal.actor,
            action="catalog.sync.start",
            target_type="catalog",
            target_id="azure",
            request_id=rid,
        ),
    )
    handle = await temporal.start_workflow(
        SyncCatalogWorkflow.run,
        CatalogSyncInput(requested_by=str(principal.user_id)),
        id=f"catalog-sync-{uuid.uuid4().hex[:8]}",
        task_queue=settings.connector_task_queue,
        execution_timeout=timedelta(minutes=30),
    )
    return {"workflow_id": handle.id}


class CompareRequest(BaseModel):
    resource_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    target_provider: str = "azure"
    target_region: str
    strategy: Strategy = Strategy.LIKE_FOR_LIKE
    headroom: float = Field(0.3, ge=0, le=2)
    options: CostOptions = Field(default_factory=CostOptions)


class CompareItem(BaseModel):
    resource_id: uuid.UUID
    name: str | None
    native_id: str
    region: str
    source_sku: str | None
    sizing: SizingResult
    cost: VmCost | None


class CompareResponse(BaseModel):
    target_provider: str
    target_region: str
    strategy: Strategy
    currency: str
    fx_per_usd: float
    fx_date: str | None
    target_prices_as_of: datetime | None
    target_price_stale: bool
    source_prices_available: bool
    items: list[CompareItem]
    totals: dict[str, Any]


def _sum(values: list[float | None]) -> float | None:
    return None if any(v is None for v in values) or not values else round(sum(v or 0 for v in values), 2)


@router.post("/workspaces/{workspace_id}/compare", response_model=CompareResponse)
async def compare(body: CompareRequest, ctx: Analyst, rid: RequestId) -> CompareResponse:
    if body.target_provider != "azure":
        raise ValidationFailedError("only Azure targets are priced in this release")
    rows = (
        (
            await ctx.session.execute(
                select(Resource).where(
                    Resource.id.in_(body.resource_ids), Resource.type == ResourceType.VM.value
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFoundError("no virtual machines found for the given ids")

    target = await load_region(ctx.session, "azure", body.target_region)
    if not target.prices:
        raise ValidationFailedError(
            f"no Azure prices for region '{body.target_region}'; run a catalog sync or choose a priced region"
        )
    currency = body.options.currency.upper()
    fx = await load_fx(ctx.session, currency)
    if fx is None and currency != "USD":
        raise ValidationFailedError(f"no exchange rate for {currency}; run a catalog sync")

    source_cache: dict[str, Any] = {}
    items: list[CompareItem] = []
    for r in sorted(rows, key=lambda x: x.name or x.native_id):
        vm = VmSpec.model_validate(r.spec)
        if r.region not in source_cache:
            source_cache[r.region] = await load_region(ctx.session, r.provider, r.region)
        src = source_cache[r.region]
        sizing = recommend(vm, target.specs, target.hourly, body.strategy, headroom=body.headroom)
        chosen = sizing.candidates[0].sku if sizing.candidates else None
        cost = estimate(vm, chosen, target.hourly, target.disks, src.hourly, src.disks, body.options)
        items.append(
            CompareItem(
                resource_id=r.id,
                name=r.name,
                native_id=r.native_id,
                region=r.region,
                source_sku=vm.source_sku,
                sizing=sizing,
                cost=cost,
            )
        )

    models = [m.value for m in (PriceModel.ON_DEMAND, PriceModel.RESERVED_1Y, PriceModel.RESERVED_3Y)]
    costs = [i.cost for i in items if i.cost]
    totals = {
        "source_monthly_usd": {m: _sum([c.source.total_monthly_usd.get(m) for c in costs]) for m in models},
        "target_monthly_usd": {m: _sum([c.target.total_monthly_usd.get(m) for c in costs]) for m in models},
        "one_time_usd": _sum([c.one_time.egress_usd + (c.one_time.dual_running_usd or 0) for c in costs]),
        "unsized": sum(1 for i in items if not i.sizing.candidates),
    }
    stale = bool(
        target.prices_fetched_at
        and datetime.now(target.prices_fetched_at.tzinfo) - target.prices_fetched_at > STALE_AFTER
    )
    await record(
        ctx.session,
        AuditRecord(
            actor=ctx.principal.actor,
            action="cost.compare",
            workspace_id=ctx.workspace_id,
            target_type="resources",
            target_id=f"{len(items)} vms",
            details={
                "target": f"azure/{body.target_region}",
                "strategy": body.strategy.value,
                "currency": currency,
                "resource_ids": [str(i.resource_id) for i in items][:50],
            },
            request_id=rid,
        ),
    )
    return CompareResponse(
        target_provider="azure",
        target_region=body.target_region,
        strategy=body.strategy,
        currency=currency,
        fx_per_usd=fx.per_usd if fx else 1.0,
        fx_date=str(fx.rate_date) if fx else None,
        target_prices_as_of=target.prices_fetched_at,
        target_price_stale=stale,
        source_prices_available=any(c.source.total_monthly_usd.get("on_demand") is not None for c in costs),
        items=items,
        totals=totals,
    )
