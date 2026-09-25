"""Cost comparison router — Phase 5.

Endpoints:
  POST /cost/compare  — run sizing + cost engine, return CostComparison
  GET  /cost/catalog/status — catalog freshness per provider
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import AuthenticatedUser
from api.dependencies import get_db_session, get_workspace_id, require_role

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/cost", tags=["cost"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CostCompareRequest(BaseModel):
    resource_id: str
    target_providers: list[str]
    target_regions: dict[str, str]          # provider → region
    scenarios: list[str] = ["on_demand"]
    currency: str = "USD"


class CostBreakdownResponse(BaseModel):
    compute_monthly: str
    storage_monthly: str
    network_monthly: str
    migration_egress: str
    dual_run: str
    total_monthly: str
    total_first_year: str
    currency: str
    fx_rate: str | None
    catalog_version: str
    catalog_date: str
    assumptions: list[str]
    is_estimate: bool = True


class CostTargetResponse(BaseModel):
    sku: str
    provider: str
    region: str
    scenario: str
    breakdown: CostBreakdownResponse
    licence_review_required: bool
    warnings: list[str]


class CostCompareResponse(BaseModel):
    resource_id: str
    source_list_monthly_usd: str | None
    source_actual_monthly_usd: str | None
    targets: list[CostTargetResponse]
    catalog_version: str
    snapshot_id: str


class CatalogProviderStatus(BaseModel):
    provider: str
    version: str | None
    effective_from: str | None
    age_hours: float | None
    is_stale: bool


class CatalogStatusResponse(BaseModel):
    providers: list[CatalogProviderStatus]
    checked_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/compare", response_model=CostCompareResponse)
async def cost_compare(
    request: CostCompareRequest,
    workspace_id: uuid.UUID = Depends(get_workspace_id),
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("analyst")),
) -> CostCompareResponse:
    """Run sizing + cost comparison for a given resource.

    Calls SizingEngine then CostEngine — no LLM involvement.
    All figures are estimates from the catalog; never a quote.
    """
    from core.models import CostSpec, DiskSpec, MetricsSpec, ProviderName, VMSpec
    from cost.engine import CostEngine, CostScenario
    from db.models import ResourceRow
    from sizing.engine import SizingEngine, SizingStrategy

    # Load resource
    from sqlalchemy import select

    result = await session.execute(
        select(ResourceRow).where(
            ResourceRow.id == uuid.UUID(request.resource_id),
            ResourceRow.workspace_id == workspace_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found")

    spec = row.spec or {}

    disks = [
        DiskSpec(
            size_gib=d.get("size_gib"),
            type_class=d.get("type_class"),
            iops=d.get("iops"),
            throughput_mbps=d.get("throughput_mbps"),
        )
        for d in spec.get("disks", [])
    ]

    metrics_data = spec.get("metrics") or {}
    metrics: MetricsSpec | None = None
    if metrics_data:
        metrics = MetricsSpec(
            **{k: v for k, v in metrics_data.items() if k in MetricsSpec.model_fields}
        )

    try:
        provider = ProviderName(row.provider)
    except ValueError:
        provider = ProviderName.aws

    cost_spec: CostSpec | None = None
    if spec.get("cost"):
        cost_spec = CostSpec(**spec["cost"])

    source_vm = VMSpec(
        id=row.id,
        workspace_id=row.workspace_id,
        connection_id=row.connection_id,
        provider=provider,
        native_id=row.native_id,
        account=row.account,
        region=row.region,
        vcpu=spec.get("vcpu"),
        memory_gib=spec.get("memory_gib"),
        gpu_count=spec.get("gpu_count"),
        gpu_model=spec.get("gpu_model"),
        instance_type=spec.get("instance_type"),
        architecture=spec.get("architecture", "x86_64"),
        os_name=spec.get("os_name"),
        license_model=spec.get("license_model"),
        disks=disks,
        metrics=metrics,
        cost=cost_spec,
        snapshot_id=row.snapshot_id,
    )

    # Parse scenarios
    valid_scenarios: list[CostScenario] = []
    for s in request.scenarios:
        try:
            valid_scenarios.append(CostScenario(s))
        except ValueError:
            log.warning("cost_compare.unknown_scenario", scenario=s)
    if not valid_scenarios:
        valid_scenarios = [CostScenario.on_demand]

    # Run sizing then cost
    sizing_engine = SizingEngine()
    cost_engine = CostEngine()
    all_sizing_results = []
    target_regions_map: dict[ProviderName, str] = {}

    for provider_str in request.target_providers:
        try:
            tgt_provider = ProviderName(provider_str)
        except ValueError:
            log.warning("cost_compare.unknown_provider", provider=provider_str)
            continue

        tgt_region = request.target_regions.get(provider_str, "")
        if not tgt_region:
            continue

        target_regions_map[tgt_provider] = tgt_region

        sizing_results = await sizing_engine.recommend(
            source_vm=source_vm,
            target_provider=tgt_provider,
            target_region=tgt_region,
            strategies=[SizingStrategy.like_for_like, SizingStrategy.right_sized],
            db=session,
        )
        all_sizing_results.extend(sizing_results)

    comparison = await cost_engine.compare(
        source_vm=source_vm,
        sizing_results=all_sizing_results,
        scenarios=valid_scenarios,
        target_regions=target_regions_map,
        workspace_currency=request.currency,
        db=session,
        snapshot_id=str(row.snapshot_id) if row.snapshot_id else "",
    )

    # Format response
    targets_out: list[CostTargetResponse] = []
    for t in comparison.targets:
        bd = t.breakdown
        targets_out.append(
            CostTargetResponse(
                sku=t.sku,
                provider=t.provider.value,
                region=t.region,
                scenario=t.scenario.value,
                breakdown=CostBreakdownResponse(
                    compute_monthly=str(bd.compute_monthly_usd),
                    storage_monthly=str(bd.storage_monthly_usd),
                    network_monthly=str(bd.network_monthly_usd),
                    migration_egress=str(bd.migration_egress_usd),
                    dual_run=str(bd.dual_run_cost_usd),
                    total_monthly=str(bd.total_monthly_usd),
                    total_first_year=str(bd.total_first_year_usd),
                    currency=bd.currency,
                    fx_rate=str(bd.fx_rate) if bd.fx_rate else None,
                    catalog_version=bd.catalog_version,
                    catalog_date=bd.catalog_date.isoformat(),
                    assumptions=bd.assumptions,
                    is_estimate=True,
                ),
                licence_review_required=t.licence_review_required,
                warnings=t.warnings,
            )
        )

    return CostCompareResponse(
        resource_id=request.resource_id,
        source_list_monthly_usd=str(comparison.source_list_monthly_usd)
        if comparison.source_list_monthly_usd is not None else None,
        source_actual_monthly_usd=str(comparison.source_actual_monthly_usd)
        if comparison.source_actual_monthly_usd is not None else None,
        targets=targets_out,
        catalog_version=comparison.catalog_version,
        snapshot_id=comparison.snapshot_id,
    )


@router.get("/catalog/status", response_model=CatalogStatusResponse)
async def catalog_status(
    session: AsyncSession = Depends(get_db_session),
    user: AuthenticatedUser = Depends(require_role("viewer")),
) -> CatalogStatusResponse:
    """Return catalog freshness for each supported provider."""
    from catalog.versioning import get_latest_catalog_version, is_catalog_stale

    providers = ["aws", "azure", "gcp", "ibm"]
    statuses: list[CatalogProviderStatus] = []

    for p in providers:
        version_info = await get_latest_catalog_version(session, p)
        stale = await is_catalog_stale(session, p)

        if version_info:
            version, effective_from = version_info
            if effective_from.tzinfo is None:
                effective_from = effective_from.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - effective_from).total_seconds() / 3600
            statuses.append(
                CatalogProviderStatus(
                    provider=p,
                    version=version,
                    effective_from=effective_from.isoformat(),
                    age_hours=round(age_hours, 1),
                    is_stale=stale,
                )
            )
        else:
            statuses.append(
                CatalogProviderStatus(
                    provider=p,
                    version=None,
                    effective_from=None,
                    age_hours=None,
                    is_stale=True,
                )
            )

    return CatalogStatusResponse(
        providers=statuses,
        checked_at=datetime.now(UTC).isoformat(),
    )
