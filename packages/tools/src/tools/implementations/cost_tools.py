"""cost.compare tool — Phase 5 implementation."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CostCompareInput(BaseModel):
    resource_id: str
    target_providers: list[str]
    target_regions: dict[str, str]          # provider → region
    scenarios: list[str] = ["on_demand"]
    currency: str = "USD"


class CostBreakdownOut(BaseModel):
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
    is_estimate: bool


class CostTargetOut(BaseModel):
    sku: str
    provider: str
    region: str
    scenario: str
    breakdown: CostBreakdownOut
    licence_review_required: bool
    warnings: list[str]


class CostCompareOutput(BaseModel):
    resource_id: str
    source_list_monthly_usd: str | None
    source_actual_monthly_usd: str | None
    targets: list[CostTargetOut]
    catalog_version: str
    snapshot_id: str


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

COST_COMPARE_TOOL = ToolDefinition(
    name="cost.compare",
    description=(
        "Compare the cost of running a VM on the current provider versus one or more "
        "target providers. Returns cost breakdowns for on-demand and reserved pricing "
        "scenarios. All values come from the catalog — not from the LLM. "
        "Always shown as estimates — not quotes."
    ),
    input_schema=CostCompareInput,
    output_schema=CostCompareOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["cost"],
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def cost_compare_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Run the cost comparison engine for a given resource."""
    from core.models import DiskSpec, MetricsSpec, ProviderName, VMSpec
    from cost.engine import CostEngine, CostScenario
    from db.models import ResourceRow
    from sizing.engine import SizingEngine, SizingStrategy

    inp = CostCompareInput(**input_data)

    # ------------------------------------------------------------------
    # Load resource from DB
    # ------------------------------------------------------------------
    result = await db.execute(
        select(ResourceRow).where(
            ResourceRow.id == uuid.UUID(inp.resource_id),
            ResourceRow.workspace_id == uuid.UUID(user.workspace_id),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"error": f"Resource {inp.resource_id} not found"}

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
        metrics = MetricsSpec(**{k: v for k, v in metrics_data.items() if k in MetricsSpec.model_fields})

    try:
        provider = ProviderName(row.provider)
    except ValueError:
        provider = ProviderName.aws

    from core.models import CostSpec

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

    # ------------------------------------------------------------------
    # Parse scenarios
    # ------------------------------------------------------------------
    valid_scenarios: list[CostScenario] = []
    for s in inp.scenarios:
        try:
            valid_scenarios.append(CostScenario(s))
        except ValueError:
            pass
    if not valid_scenarios:
        valid_scenarios = [CostScenario.on_demand]

    # ------------------------------------------------------------------
    # Run sizing then cost engine for each target provider
    # ------------------------------------------------------------------
    sizing_engine = SizingEngine()
    cost_engine = CostEngine()
    all_sizing_results = []
    target_regions_map: dict[ProviderName, str] = {}

    for provider_str in inp.target_providers:
        try:
            tgt_provider = ProviderName(provider_str)
        except ValueError:
            continue

        tgt_region = inp.target_regions.get(provider_str, "")
        if not tgt_region:
            continue

        target_regions_map[tgt_provider] = tgt_region

        sizing_results = await sizing_engine.recommend(
            source_vm=source_vm,
            target_provider=tgt_provider,
            target_region=tgt_region,
            strategies=[SizingStrategy.like_for_like, SizingStrategy.right_sized],
            db=db,
        )
        all_sizing_results.extend(sizing_results)

    comparison = await cost_engine.compare(
        source_vm=source_vm,
        sizing_results=all_sizing_results,
        scenarios=valid_scenarios,
        target_regions=target_regions_map,
        workspace_currency=inp.currency,
        db=db,
        snapshot_id=str(row.snapshot_id) if row.snapshot_id else "",
    )

    # ------------------------------------------------------------------
    # Format output
    # ------------------------------------------------------------------
    def _dec(v: Decimal | None) -> str | None:
        return str(v) if v is not None else None

    targets_out: list[CostTargetOut] = []
    for t in comparison.targets:
        bd = t.breakdown
        targets_out.append(
            CostTargetOut(
                sku=t.sku,
                provider=t.provider.value,
                region=t.region,
                scenario=t.scenario.value,
                breakdown=CostBreakdownOut(
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

    output = CostCompareOutput(
        resource_id=inp.resource_id,
        source_list_monthly_usd=_dec(comparison.source_list_monthly_usd),
        source_actual_monthly_usd=_dec(comparison.source_actual_monthly_usd),
        targets=targets_out,
        catalog_version=comparison.catalog_version,
        snapshot_id=comparison.snapshot_id,
    )
    return output.model_dump()
