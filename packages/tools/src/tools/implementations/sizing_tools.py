"""sizing.recommend tool — Phase 5 implementation."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tools.registry import CurrentUser, ToolDefinition

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SizingRecommendInput(BaseModel):
    resource_id: str
    target_provider: str
    target_region: str
    strategies: list[str] = ["right_sized"]
    headroom_pct: float = 0.30


class CandidateSKU(BaseModel):
    sku: str
    vcpu: int
    memory_gib: float
    cpu_arch: str
    gpu_count: int
    on_demand_price_usd: float | None
    reasoning: str
    catalog_version: str


class StrategyCandidates(BaseModel):
    strategy: str
    candidates: list[CandidateSKU]
    warnings: list[str]
    catalog_version: str
    catalog_date: str


class SizingRecommendOutput(BaseModel):
    resource_id: str
    source_vcpu: int | None
    source_memory_gib: float | None
    source_arch: str | None
    target_provider: str
    target_region: str
    results: list[StrategyCandidates]


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

SIZING_RECOMMEND_TOOL = ToolDefinition(
    name="sizing.recommend",
    description=(
        "Recommend target SKUs for migrating a VM resource to a different provider. "
        "Uses catalog instance type data and observed utilisation metrics. "
        "Strategies: right_sized (p95+headroom), like_for_like (exact match), "
        "cheapest_fit (minimum requirements, lowest price). "
        "All numbers come from the catalog — no LLM estimates."
    ),
    input_schema=SizingRecommendInput,
    output_schema=SizingRecommendOutput,
    side_effect_class="read",
    required_role="analyst",
    tags=["sizing"],
)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def sizing_recommend_handler(
    input_data: dict[str, Any],
    user: CurrentUser,
    db: AsyncSession,
) -> dict[str, Any]:
    """Recommend target instance types for a given resource."""
    from core.models import DiskSpec, MetricsSpec, ProviderName, VMSpec
    from db.models import ResourceRow
    from sizing.engine import SizingEngine, SizingStrategy

    inp = SizingRecommendInput(**input_data)

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

    # Build VMSpec from ResourceRow.spec
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
        snapshot_id=row.snapshot_id,
    )

    # ------------------------------------------------------------------
    # Parse strategies
    # ------------------------------------------------------------------
    valid_strategies: list[SizingStrategy] = []
    for s in inp.strategies:
        try:
            valid_strategies.append(SizingStrategy(s))
        except ValueError:
            pass
    if not valid_strategies:
        valid_strategies = [SizingStrategy.right_sized]

    try:
        target_provider = ProviderName(inp.target_provider)
    except ValueError:
        return {"error": f"Unknown provider: {inp.target_provider}"}

    # ------------------------------------------------------------------
    # Run sizing engine
    # ------------------------------------------------------------------
    engine = SizingEngine()
    sizing_results = await engine.recommend(
        source_vm=source_vm,
        target_provider=target_provider,
        target_region=inp.target_region,
        strategies=valid_strategies,
        db=db,
        headroom_pct=inp.headroom_pct,
    )

    # ------------------------------------------------------------------
    # Format output
    # ------------------------------------------------------------------
    strategy_outputs: list[StrategyCandidates] = []
    for r in sizing_results:
        candidates = [
            CandidateSKU(
                sku=c.sku,
                vcpu=c.vcpu,
                memory_gib=round(c.memory_mib / 1024, 2),
                cpu_arch=c.cpu_arch,
                gpu_count=c.gpu_count,
                on_demand_price_usd=c.on_demand_price_usd,
                reasoning=c.reasoning,
                catalog_version=c.catalog_version,
            )
            for c in r.candidates
        ]
        strategy_outputs.append(
            StrategyCandidates(
                strategy=r.strategy.value,
                candidates=candidates,
                warnings=r.warnings,
                catalog_version=r.catalog_version,
                catalog_date=r.catalog_date.isoformat(),
            )
        )

    output = SizingRecommendOutput(
        resource_id=inp.resource_id,
        source_vcpu=source_vm.vcpu,
        source_memory_gib=source_vm.memory_gib,
        source_arch=source_vm.architecture,
        target_provider=inp.target_provider,
        target_region=inp.target_region,
        results=strategy_outputs,
    )
    return output.model_dump()
