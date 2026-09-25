"""AETHER MIGRATE — cost engine (Phase 5).

Computes cost breakdowns and cross-provider comparisons.
No LLM calls — all numbers come from catalog data and deterministic arithmetic.

Per §13.3:
  - Compute: catalog price × hours/month × scenario multiplier
  - Storage: per disk, look up disk price catalog
  - Network: p95 egress estimate or assumed
  - Migration egress: disk GiB × source egress rate
  - Dual-run: 14 days × daily on-demand
  - FX: latest FXRateRow for workspace currency

IMPORTANT: ``CostBreakdown.is_estimate`` is always True.  The model enforces
this via a Literal type — the field cannot be set to False.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

import structlog
from core.models import ProviderName, VMSpec
from pydantic import BaseModel, model_validator
from sizing.engine import SizingResult
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

_HOURS_PER_MONTH: Decimal = Decimal("730")
_DAYS_PER_DUAL_RUN: int = 14
_HOURS_PER_DUAL_RUN: Decimal = Decimal(str(_DAYS_PER_DUAL_RUN * 24))

# Approximate egress rates per GiB by provider (USD)
_EGRESS_RATE_PER_GIB: dict[str, Decimal] = {
    "aws": Decimal("0.09"),
    "azure": Decimal("0.08"),
    "gcp": Decimal("0.08"),
    "ibm": Decimal("0.09"),
}

# Steady-state egress rate per GB·hour for network estimate
_STEADY_EGRESS_PER_GBPS_HOUR: Decimal = Decimal("0.09")


# ---------------------------------------------------------------------------
# Public enumerations and models
# ---------------------------------------------------------------------------


class CostScenario(str, Enum):
    on_demand = "on_demand"
    reserved_1yr_std = "reserved_1yr_std"
    reserved_3yr_std = "reserved_3yr_std"
    reserved_1yr_conv = "reserved_1yr_conv"
    reserved_3yr_conv = "reserved_3yr_conv"


# Map CostScenario → catalog term string
_SCENARIO_TO_TERM: dict[CostScenario, str] = {
    CostScenario.on_demand: "on-demand",
    CostScenario.reserved_1yr_std: "1yr-std",
    CostScenario.reserved_3yr_std: "3yr-std",
    CostScenario.reserved_1yr_conv: "1yr-conv",
    CostScenario.reserved_3yr_conv: "3yr-conv",
}


class CostBreakdown(BaseModel):
    """Full cost breakdown for a single target SKU + scenario."""

    compute_monthly_usd: Decimal
    storage_monthly_usd: Decimal
    network_monthly_usd: Decimal          # steady-state egress estimate
    migration_egress_usd: Decimal         # one-time
    dual_run_days: int = _DAYS_PER_DUAL_RUN
    dual_run_cost_usd: Decimal
    total_monthly_usd: Decimal
    total_first_year_usd: Decimal         # 12 months + migration_egress + dual_run
    assumptions: list[str]
    catalog_version: str
    catalog_date: datetime
    currency: str
    fx_rate: Decimal | None
    # Always True — enforced by Literal; never a quote
    is_estimate: Literal[True] = True

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="before")
    @classmethod
    def _force_is_estimate(cls, values: dict[str, Any]) -> dict[str, Any]:
        values["is_estimate"] = True
        return values


class CostTarget(BaseModel):
    """Cost data for a single target candidate SKU."""

    sku: str
    provider: ProviderName
    region: str
    scenario: CostScenario
    breakdown: CostBreakdown
    licence_review_required: bool = False
    warnings: list[str] = []


class CostComparison(BaseModel):
    """Full cost comparison result."""

    source_list_monthly_usd: Decimal | None
    source_actual_monthly_usd: Decimal | None  # from billing, nullable
    targets: list[CostTarget]
    snapshot_id: str
    catalog_version: str

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Cost Engine
# ---------------------------------------------------------------------------


class CostEngine:
    """Deterministic cost modeling and comparison engine."""

    async def compare(
        self,
        source_vm: VMSpec,
        sizing_results: list[SizingResult],
        scenarios: list[CostScenario],
        target_regions: dict[ProviderName, str],
        workspace_currency: str,
        db: AsyncSession,
        snapshot_id: str = "",
    ) -> CostComparison:
        """Build a full cost comparison for *source_vm* against sizing results.

        Parameters
        ----------
        source_vm:
            Source VMSpec (may include cost.actual_monthly_cost).
        sizing_results:
            Output from SizingEngine.recommend() for one or more providers.
        scenarios:
            Pricing scenarios to evaluate.
        target_regions:
            Mapping of provider → region for the target.
        workspace_currency:
            ISO 4217 currency code (e.g. "USD", "EUR").  FX applied if not USD.
        db:
            AsyncSession for reading catalog tables.
        snapshot_id:
            Snapshot identifier for traceability.
        """
        from db.models import FXRateRow

        # ------------------------------------------------------------------
        # FX rate lookup
        # ------------------------------------------------------------------
        fx_rate: Decimal | None = None
        fx_rate_date: str = ""
        if workspace_currency.upper() != "USD":
            fx_q = (
                select(FXRateRow)
                .where(
                    FXRateRow.base_currency == "USD",
                    FXRateRow.target_currency == workspace_currency.upper(),
                )
                .order_by(desc(FXRateRow.rate_date))
                .limit(1)
            )
            fx_result = await db.execute(fx_q)
            fx_row = fx_result.scalar_one_or_none()
            if fx_row is not None:
                fx_rate = Decimal(str(fx_row.rate))
                fx_rate_date = str(fx_row.rate_date)
            else:
                log.warning("cost.fx_rate_not_found", currency=workspace_currency)

        # ------------------------------------------------------------------
        # Source VM cost (from CostSpec if available)
        # ------------------------------------------------------------------
        source_list_monthly: Decimal | None = None
        source_actual_monthly: Decimal | None = None
        if source_vm.cost:
            if source_vm.cost.list_monthly_cost is not None:
                source_list_monthly = Decimal(str(source_vm.cost.list_monthly_cost))
            if source_vm.cost.actual_monthly_cost is not None:
                source_actual_monthly = Decimal(str(source_vm.cost.actual_monthly_cost))

        # ------------------------------------------------------------------
        # Disk info for storage and migration egress
        # ------------------------------------------------------------------
        total_disk_gib = sum(
            (d.size_gib or 0) for d in (source_vm.disks or [])
        )
        disk_specs = source_vm.disks or []

        # Source provider egress rate
        src_provider = (source_vm.provider.value if source_vm.provider else "aws")
        egress_rate = _EGRESS_RATE_PER_GIB.get(src_provider, Decimal("0.09"))
        migration_egress = Decimal(str(total_disk_gib)) * egress_rate

        # ------------------------------------------------------------------
        # Build targets
        # ------------------------------------------------------------------
        targets: list[CostTarget] = []
        catalog_version = ""

        for sizing_result in sizing_results:
            if sizing_result.candidates:
                catalog_version = sizing_result.catalog_version

            provider_str = (
                sizing_result.candidates[0].provider.value
                if sizing_result.candidates else "unknown"
            )
            target_region = target_regions.get(
                ProviderName(provider_str), ""
            ) if sizing_result.candidates else ""

            catalog_date = sizing_result.catalog_date
            for candidate in sizing_result.candidates[:3]:  # top 3 per result
                for scenario in scenarios:
                    target = await self._build_target(
                        candidate=candidate,
                        scenario=scenario,
                        source_vm=source_vm,
                        disk_specs=disk_specs,
                        migration_egress=migration_egress,
                        workspace_currency=workspace_currency,
                        fx_rate=fx_rate,
                        fx_rate_date=fx_rate_date,
                        catalog_date=catalog_date,
                        db=db,
                    )
                    targets.append(target)
                    catalog_version = catalog_version or candidate.catalog_version

        return CostComparison(
            source_list_monthly_usd=source_list_monthly,
            source_actual_monthly_usd=source_actual_monthly,
            targets=targets,
            snapshot_id=snapshot_id,
            catalog_version=catalog_version,
        )

    async def _build_target(
        self,
        candidate: Any,
        scenario: CostScenario,
        source_vm: VMSpec,
        disk_specs: list[Any],
        migration_egress: Decimal,
        workspace_currency: str,
        fx_rate: Decimal | None,
        fx_rate_date: str,
        db: AsyncSession,
        catalog_date: datetime | None = None,
    ) -> CostTarget:
        from db.models import CatalogDiskPriceRow, CatalogPriceRow

        from cost.disk_mapping import map_disk_type

        assumptions: list[str] = []
        warnings: list[str] = []
        licence_review = False

        term = _SCENARIO_TO_TERM[scenario]
        provider_val = candidate.provider.value

        # ------------------------------------------------------------------
        # Compute cost
        # ------------------------------------------------------------------
        os_name = (source_vm.os_name or "").lower()
        os_type = "windows" if "windows" in os_name else "linux"

        if "windows" in os_type or "sql" in (source_vm.license_model or "").lower():
            licence_review = True
            warnings.append(
                "Windows or SQL Server licence portability requires review. "
                "Cost shown is for the instance only (+$0 for licence — not validated)."
            )

        price_q = select(CatalogPriceRow).where(
            CatalogPriceRow.provider == provider_val,
            CatalogPriceRow.region == candidate.region,
            CatalogPriceRow.sku == candidate.sku,
            CatalogPriceRow.os_type == os_type,
            CatalogPriceRow.term == term,
            CatalogPriceRow.catalog_version == candidate.catalog_version,
        ).limit(1)
        price_result = await db.execute(price_q)
        price_row = price_result.scalar_one_or_none()

        if price_row is not None:
            hourly_usd = Decimal(str(price_row.price_usd))
            assumptions.append(f"compute: {term} {os_type} @ ${hourly_usd}/hr from catalog")
        else:
            hourly_usd = Decimal("0")
            warnings.append(f"No {term} price found for {candidate.sku}; compute cost shown as $0")
            assumptions.append(f"compute: price unavailable for {candidate.sku} ({term})")

        compute_monthly = (hourly_usd * _HOURS_PER_MONTH).quantize(Decimal("0.01"))

        # ------------------------------------------------------------------
        # Storage cost
        # ------------------------------------------------------------------
        storage_monthly = Decimal("0")
        if disk_specs:
            for disk in disk_specs:
                src_type = disk.type_class or "gp3"
                tgt_type = map_disk_type(src_type, provider_val)
                disk_gib = Decimal(str(disk.size_gib or 0))

                disk_price_q = select(CatalogDiskPriceRow).where(
                    CatalogDiskPriceRow.provider == provider_val,
                    CatalogDiskPriceRow.region == candidate.region,
                    CatalogDiskPriceRow.disk_type == tgt_type,
                    CatalogDiskPriceRow.catalog_version == candidate.catalog_version,
                ).limit(1)
                dp_result = await db.execute(disk_price_q)
                dp_row = dp_result.scalar_one_or_none()

                if dp_row is not None and dp_row.price_per_gib_month is not None:
                    disk_cost = disk_gib * Decimal(str(dp_row.price_per_gib_month))
                    # Add IOPS/throughput if disk spec has them
                    if dp_row.price_per_iops_month and disk.iops:
                        disk_cost += Decimal(str(disk.iops)) * Decimal(str(dp_row.price_per_iops_month))
                    if dp_row.price_per_mbps_month and disk.throughput_mbps:
                        disk_cost += Decimal(str(disk.throughput_mbps)) * Decimal(str(dp_row.price_per_mbps_month))
                    storage_monthly += disk_cost
                    assumptions.append(
                        f"storage: {disk_gib} GiB {tgt_type} @ "
                        f"${dp_row.price_per_gib_month}/GiB/month"
                    )
                else:
                    # No disk price data; estimate based on gp3 baseline
                    estimated_disk_cost = disk_gib * Decimal("0.08")
                    storage_monthly += estimated_disk_cost
                    assumptions.append(
                        f"storage: {disk_gib} GiB {tgt_type} — no price data, "
                        f"estimated $0.08/GiB/month"
                    )
        else:
            assumptions.append("storage: no disk data, $0 assumed")

        storage_monthly = storage_monthly.quantize(Decimal("0.01"))

        # ------------------------------------------------------------------
        # Network steady-state egress
        # ------------------------------------------------------------------
        net_monthly = Decimal("0")
        if source_vm.metrics and source_vm.metrics.net_mbps_p95 is not None:
            # net_mbps_p95 → GB/month: Mbps * (730h * 3600s) / (8 bits/byte) / 1024 MB/GB
            net_gbps = Decimal(str(source_vm.metrics.net_mbps_p95)) / Decimal("1000")
            gb_per_month = net_gbps * Decimal("3600") * _HOURS_PER_MONTH / Decimal("8") / Decimal("1024")
            net_monthly = (gb_per_month * Decimal("0.09")).quantize(Decimal("0.01"))
            assumptions.append(
                f"network: {source_vm.metrics.net_mbps_p95:.0f} Mbps p95 → "
                f"${net_monthly}/month egress estimate"
            )
        else:
            assumptions.append("network: no metrics available; $0 assumed — provide estimate if needed")

        # ------------------------------------------------------------------
        # Dual-run cost (14 days × source on-demand)
        # ------------------------------------------------------------------
        if source_vm.cost and source_vm.cost.list_monthly_cost:
            daily_source = Decimal(str(source_vm.cost.list_monthly_cost)) / Decimal("30")
            dual_run_cost = (daily_source * Decimal(_DAYS_PER_DUAL_RUN)).quantize(Decimal("0.01"))
            assumptions.append(
                f"dual-run: {_DAYS_PER_DUAL_RUN} days × source list monthly / 30"
            )
        else:
            # Fallback: use target on-demand price for dual-run estimate
            dual_run_cost = (hourly_usd * _HOURS_PER_DUAL_RUN).quantize(Decimal("0.01"))
            assumptions.append(
                f"dual-run: {_DAYS_PER_DUAL_RUN} days at target on-demand price "
                f"(source cost unavailable)"
            )

        # ------------------------------------------------------------------
        # Totals
        # ------------------------------------------------------------------
        total_monthly = (compute_monthly + storage_monthly + net_monthly).quantize(Decimal("0.01"))
        total_first_year = (
            total_monthly * Decimal("12") + migration_egress + dual_run_cost
        ).quantize(Decimal("0.01"))

        # ------------------------------------------------------------------
        # FX conversion
        # ------------------------------------------------------------------
        effective_currency = workspace_currency.upper()
        effective_fx = fx_rate

        if effective_fx is not None and effective_currency != "USD":
            compute_monthly = (compute_monthly * effective_fx).quantize(Decimal("0.01"))
            storage_monthly = (storage_monthly * effective_fx).quantize(Decimal("0.01"))
            net_monthly = (net_monthly * effective_fx).quantize(Decimal("0.01"))
            dual_run_cost = (dual_run_cost * effective_fx).quantize(Decimal("0.01"))
            migration_egress_converted = (migration_egress * effective_fx).quantize(Decimal("0.01"))
            total_monthly = (total_monthly * effective_fx).quantize(Decimal("0.01"))
            total_first_year = (total_first_year * effective_fx).quantize(Decimal("0.01"))
            assumptions.append(
                f"FX: USD→{effective_currency} @ {effective_fx} (ECB {fx_rate_date})"
            )
        else:
            migration_egress_converted = migration_egress.quantize(Decimal("0.01"))
            if effective_currency == "USD":
                effective_fx = None

        assumptions.append("estimate — not a quote")

        # Check catalog staleness warning (catalog_date comes from SizingResult)
        catalog_date_raw = catalog_date if catalog_date is not None else datetime.now(UTC)
        if isinstance(catalog_date_raw, datetime):
            cd = catalog_date_raw
            if cd.tzinfo is None:
                cd = cd.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - cd).total_seconds() / 3600
            if age_hours > 48:
                warnings.append(
                    f"Catalog data is {age_hours:.0f}h old (>48h). "
                    "Prices may be stale — consider triggering a catalog sync."
                )

        breakdown = CostBreakdown(
            compute_monthly_usd=compute_monthly,
            storage_monthly_usd=storage_monthly,
            network_monthly_usd=net_monthly,
            migration_egress_usd=migration_egress_converted,
            dual_run_days=_DAYS_PER_DUAL_RUN,
            dual_run_cost_usd=dual_run_cost,
            total_monthly_usd=total_monthly,
            total_first_year_usd=total_first_year,
            assumptions=assumptions,
            catalog_version=candidate.catalog_version,
            catalog_date=catalog_date_raw,
            currency=effective_currency,
            fx_rate=effective_fx,
            is_estimate=True,
        )

        return CostTarget(
            sku=candidate.sku,
            provider=candidate.provider,
            region=candidate.region,
            scenario=scenario,
            breakdown=breakdown,
            licence_review_required=licence_review,
            warnings=warnings,
        )
