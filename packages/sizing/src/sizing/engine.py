"""AETHER MIGRATE — sizing engine (Phase 5).

Deterministic instance-type recommendation engine.  No LLM calls — only
catalog data and arithmetic.

Algorithm per §13.2:
  1. Hard constraints filter (arch, GPU, availability, restrictions, OS)
  2. Strategy-specific selection:
     - like_for_like: exact vCPU + memory match, or closest
     - right_sized: p95 metrics * (1 + headroom), fallback to allocation
     - cheapest_fit: minimum requirements, sorted by on-demand price
  3. Ranking within strategy: price ASC, generation DESC, family-match score DESC
  4. Reasoning string included for every candidate
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from core.models import ProviderName, VMSpec
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Hours per month (approximation used throughout the cost/sizing engines)
_HOURS_PER_MONTH = 730.0


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class SizingStrategy(str, Enum):
    like_for_like = "like_for_like"
    right_sized = "right_sized"
    cheapest_fit = "cheapest_fit"


class SizingCandidate(BaseModel):
    sku: str
    provider: ProviderName
    region: str
    vcpu: int
    memory_mib: int
    cpu_arch: str
    gpu_count: int
    network_bandwidth_gbps: float | None
    available: bool
    restricted: bool
    on_demand_price_usd: float | None  # hourly USD; None when no price data
    reasoning: str
    catalog_version: str


class SizingResult(BaseModel):
    strategy: SizingStrategy
    candidates: list[SizingCandidate]       # ranked best-first
    source_vm: dict[str, Any]               # key fields from source VM
    hard_constraints_applied: list[str]     # e.g. ["requires arm64", "requires GPU"]
    catalog_version: str
    catalog_date: datetime
    warnings: list[str]                     # e.g. "memory metrics unavailable"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generation_score(sku: str) -> int:
    """Heuristic generation score from SKU name (higher = newer)."""
    import re

    # Azure: Standard_D4s_v5 → 5, Standard_D4s_v3 → 3
    m = re.search(r"_v(\d+)", sku, re.IGNORECASE)
    if m:
        return int(m.group(1))

    # AWS: m5.xlarge → 5, m6i.xlarge → 6, m7g.xlarge → 7
    m = re.search(r"[a-z](\d+)", sku, re.IGNORECASE)
    if m:
        return int(m.group(1))

    return 0


def _family_match_score(source_sku: str | None, target_sku: str, provider: str) -> int:
    """Score how well the target SKU family matches the source (higher = better).

    Only meaningful for same-provider comparisons; returns 0 otherwise.
    """
    if not source_sku:
        return 0

    # Azure: both Standard_D → family match
    if provider == "azure":
        import re
        src_family = re.match(r"Standard_(\w+?)[\d]", source_sku or "")
        tgt_family = re.match(r"Standard_(\w+?)[\d]", target_sku)
        if src_family and tgt_family and src_family.group(1) == tgt_family.group(1):
            return 2

    # AWS: both m5/m6 prefix → match
    if provider == "aws":
        import re
        src_family = re.match(r"([a-z]+)\d", source_sku or "")
        tgt_family = re.match(r"([a-z]+)\d", target_sku)
        if src_family and tgt_family and src_family.group(1) == tgt_family.group(1):
            return 2

    return 0


def _requires_os_support(source_vm: VMSpec) -> str:
    """Return the OS family key required based on source VM OS."""
    os_name = (source_vm.os_name or "").lower()
    if "windows" in os_name:
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# Sizing Engine
# ---------------------------------------------------------------------------


class SizingEngine:
    """Deterministic instance type recommendation engine."""

    async def recommend(
        self,
        source_vm: VMSpec,
        target_provider: ProviderName,
        target_region: str,
        strategies: list[SizingStrategy],
        db: AsyncSession,
        headroom_pct: float = 0.30,
        max_candidates: int = 5,
    ) -> list[SizingResult]:
        """Recommend instance types for *source_vm* on *target_provider*.

        Parameters
        ----------
        source_vm:
            Source VMSpec from the inventory.
        target_provider:
            Cloud provider to recommend types for.
        target_region:
            Target region name (provider-specific).
        strategies:
            One or more SizingStrategy values to compute.
        db:
            AsyncSession for reading catalog tables.
        headroom_pct:
            Additional headroom above p95 metrics for right_sized strategy.
        max_candidates:
            Maximum number of candidates to return per strategy.
        """
        from db.models import CatalogInstanceTypeRow, CatalogPriceRow

        # ------------------------------------------------------------------
        # Fetch candidate instance types from catalog
        # ------------------------------------------------------------------
        it_query = select(CatalogInstanceTypeRow).where(
            CatalogInstanceTypeRow.provider == target_provider.value,
            CatalogInstanceTypeRow.region == target_region,
            CatalogInstanceTypeRow.available_in_region.is_(True),
            CatalogInstanceTypeRow.restricted.is_(False),
        )
        it_result = await db.execute(it_query)
        all_rows = it_result.scalars().all()

        if not all_rows:
            log.warning(
                "sizing.no_candidates",
                provider=target_provider.value,
                region=target_region,
            )
            return []

        # Catalog version = version of the most-recent records
        catalog_version = all_rows[0].catalog_version
        catalog_date_raw = all_rows[0].effective_from
        if catalog_date_raw.tzinfo is None:
            catalog_date = catalog_date_raw.replace(tzinfo=UTC)
        else:
            catalog_date = catalog_date_raw

        # ------------------------------------------------------------------
        # Build on-demand price lookup: sku → hourly_usd
        # ------------------------------------------------------------------
        os_key = _requires_os_support(source_vm)
        price_query = select(CatalogPriceRow).where(
            CatalogPriceRow.provider == target_provider.value,
            CatalogPriceRow.region == target_region,
            CatalogPriceRow.os_type == os_key,
            CatalogPriceRow.term == "on-demand",
            CatalogPriceRow.catalog_version == catalog_version,
        )
        price_result = await db.execute(price_query)
        price_map: dict[str, float] = {
            row.sku: float(row.price_usd) for row in price_result.scalars().all()
        }

        # ------------------------------------------------------------------
        # Hard constraints
        # ------------------------------------------------------------------
        constraints_applied: list[str] = []
        source_arch = source_vm.architecture or "x86_64"
        source_vcpu = source_vm.vcpu or 0
        source_mem_mib = int((source_vm.memory_gib or 0) * 1024)
        source_gpu_count = source_vm.gpu_count or 0
        source_gpu_model = source_vm.gpu_model

        # Filter by CPU arch
        constraints_applied.append(f"cpu_arch={source_arch}")
        filtered = [r for r in all_rows if r.cpu_arch == source_arch]

        # Filter by GPU requirements
        if source_gpu_count > 0:
            constraints_applied.append(f"gpu_count>={source_gpu_count}")
            filtered = [r for r in filtered if r.gpu_count >= source_gpu_count]
            if source_gpu_model:
                constraints_applied.append(f"gpu_model≈{source_gpu_model}")
                # Soft match — include if model name partially matches
                filtered_gpu = [
                    r for r in filtered
                    if r.gpu_model and source_gpu_model.lower() in r.gpu_model.lower()
                ]
                if filtered_gpu:
                    filtered = filtered_gpu

        # Filter by OS support
        constraints_applied.append(f"os={os_key}")
        filtered = [r for r in filtered if r.os_support.get(os_key, True)]

        warnings: list[str] = []

        source_dict: dict[str, Any] = {
            "instance_type": source_vm.instance_type,
            "vcpu": source_vcpu,
            "memory_gib": source_vm.memory_gib,
            "architecture": source_arch,
            "gpu_count": source_gpu_count,
            "provider": source_vm.provider.value if source_vm.provider else None,
            "region": source_vm.region,
        }

        results: list[SizingResult] = []

        for strategy in strategies:
            candidates = self._apply_strategy(
                strategy=strategy,
                filtered=filtered,
                price_map=price_map,
                source_vm=source_vm,
                source_vcpu=source_vcpu,
                source_mem_mib=source_mem_mib,
                target_provider=target_provider,
                target_region=target_region,
                headroom_pct=headroom_pct,
                max_candidates=max_candidates,
                catalog_version=catalog_version,
                warnings=warnings,
            )

            results.append(
                SizingResult(
                    strategy=strategy,
                    candidates=candidates,
                    source_vm=source_dict,
                    hard_constraints_applied=constraints_applied,
                    catalog_version=catalog_version,
                    catalog_date=catalog_date,
                    warnings=list(warnings),
                )
            )

        return results

    def _apply_strategy(
        self,
        strategy: SizingStrategy,
        filtered: list[Any],
        price_map: dict[str, float],
        source_vm: VMSpec,
        source_vcpu: int,
        source_mem_mib: int,
        target_provider: ProviderName,
        target_region: str,
        headroom_pct: float,
        max_candidates: int,
        catalog_version: str,
        warnings: list[str],
    ) -> list[SizingCandidate]:
        if strategy == SizingStrategy.like_for_like:
            return self._like_for_like(
                filtered, price_map, source_vm, source_vcpu, source_mem_mib,
                target_provider, target_region, catalog_version, max_candidates,
            )
        if strategy == SizingStrategy.right_sized:
            return self._right_sized(
                filtered, price_map, source_vm, source_vcpu, source_mem_mib,
                target_provider, target_region, catalog_version, headroom_pct,
                max_candidates, warnings,
            )
        if strategy == SizingStrategy.cheapest_fit:
            return self._cheapest_fit(
                filtered, price_map, source_vm, source_vcpu, source_mem_mib,
                target_provider, target_region, catalog_version, max_candidates,
            )
        return []

    def _rank(
        self,
        rows: list[Any],
        price_map: dict[str, float],
        source_sku: str | None,
        provider: str,
    ) -> list[Any]:
        """Sort rows: price ASC, generation DESC, family-match DESC."""
        def _key(r: Any) -> tuple[float, int, int]:
            price = price_map.get(r.sku, float("inf"))
            gen = _generation_score(r.sku)
            family = _family_match_score(source_sku, r.sku, provider)
            return (price, -gen, -family)

        return sorted(rows, key=_key)

    def _make_candidate(
        self,
        row: Any,
        price_map: dict[str, float],
        reasoning: str,
        catalog_version: str,
        target_provider: ProviderName,
        target_region: str,
    ) -> SizingCandidate:
        return SizingCandidate(
            sku=row.sku,
            provider=target_provider,
            region=target_region,
            vcpu=row.vcpu,
            memory_mib=row.memory_mib,
            cpu_arch=row.cpu_arch,
            gpu_count=row.gpu_count,
            network_bandwidth_gbps=row.network_bandwidth_gbps,
            available=row.available_in_region,
            restricted=row.restricted,
            on_demand_price_usd=price_map.get(row.sku),
            reasoning=reasoning,
            catalog_version=catalog_version,
        )

    # ------------------------------------------------------------------
    # Strategy: like_for_like
    # ------------------------------------------------------------------

    def _like_for_like(
        self,
        filtered: list[Any],
        price_map: dict[str, float],
        source_vm: VMSpec,
        source_vcpu: int,
        source_mem_mib: int,
        target_provider: ProviderName,
        target_region: str,
        catalog_version: str,
        max_candidates: int,
    ) -> list[SizingCandidate]:
        """Exact match on vCPU + memory, falling back to closest match."""
        exact = [
            r for r in filtered
            if r.vcpu == source_vcpu and r.memory_mib == source_mem_mib
        ]

        if not exact:
            # Closest match: minimize L2 distance in (vcpu, memory) space (normalized)
            def _dist(r: Any) -> float:
                dv = (r.vcpu - source_vcpu) / max(source_vcpu, 1)
                dm = (r.memory_mib - source_mem_mib) / max(source_mem_mib, 1)
                return dv * dv + dm * dm

            candidates_pool = sorted(filtered, key=_dist)[:max_candidates * 3]
            reason_prefix = "like_for_like (closest match)"
        else:
            candidates_pool = exact
            reason_prefix = "like_for_like"

        ranked = self._rank(
            candidates_pool, price_map, source_vm.instance_type, target_provider.value
        )[:max_candidates]

        results: list[SizingCandidate] = []
        source_mem_gib = round(source_mem_mib / 1024, 1)
        for row in ranked:
            mem_gib = round(row.memory_mib / 1024, 1)
            reasoning = (
                f"{reason_prefix}: source is {source_vm.instance_type or '?'} "
                f"({source_vcpu} vCPU / {source_mem_gib} GiB). "
                f"Selected {row.sku} ({row.vcpu} vCPU / {mem_gib} GiB) "
                f"as the closest match in {target_provider.value} {target_region}."
            )
            results.append(
                self._make_candidate(
                    row, price_map, reasoning, catalog_version, target_provider, target_region
                )
            )
        return results

    # ------------------------------------------------------------------
    # Strategy: right_sized
    # ------------------------------------------------------------------

    def _right_sized(
        self,
        filtered: list[Any],
        price_map: dict[str, float],
        source_vm: VMSpec,
        source_vcpu: int,
        source_mem_mib: int,
        target_provider: ProviderName,
        target_region: str,
        catalog_version: str,
        headroom_pct: float,
        max_candidates: int,
        warnings: list[str],
    ) -> list[SizingCandidate]:
        """Minimum-fit based on p95 metrics + headroom; falls back to allocation."""
        metrics = source_vm.metrics
        using_metrics = False

        if (
            metrics is not None
            and metrics.cpu_p95 is not None
            and source_vcpu > 0
        ):
            # cpu_p95 is a percentage (0–100); convert to core count
            cpu_cores_p95 = (metrics.cpu_p95 / 100.0) * source_vcpu
            min_vcpu = math.ceil(cpu_cores_p95 * (1 + headroom_pct))
            using_metrics = True
        else:
            min_vcpu = source_vcpu
            if not any("memory metrics unavailable" in w for w in warnings):
                warnings.append(
                    "cpu metrics unavailable, using allocation for right_sized sizing"
                )

        if (
            metrics is not None
            and metrics.mem_p95 is not None
            and source_mem_mib > 0
        ):
            # mem_p95 is a percentage (0–100)
            mem_p95_mib = (metrics.mem_p95 / 100.0) * source_mem_mib
            min_mem_mib = math.ceil(mem_p95_mib * (1 + headroom_pct))
            using_metrics = True
        else:
            min_mem_mib = source_mem_mib
            if not any("memory metrics unavailable" in w for w in warnings):
                warnings.append(
                    "memory metrics unavailable, using allocation for right_sized sizing"
                )

        min_vcpu = max(min_vcpu, 1)
        min_mem_mib = max(min_mem_mib, 512)

        candidates_pool = [
            r for r in filtered
            if r.vcpu >= min_vcpu and r.memory_mib >= min_mem_mib
        ]

        ranked = self._rank(
            candidates_pool, price_map, source_vm.instance_type, target_provider.value
        )[:max_candidates]

        results: list[SizingCandidate] = []
        source_mem_gib = round(source_mem_mib / 1024, 1)
        for row in ranked:
            mem_gib = round(row.memory_mib / 1024, 1)
            if using_metrics and metrics:
                basis = (
                    f"p95 cpu={metrics.cpu_p95:.0f}% → {min_vcpu} vCPU, "
                    f"p95 mem={metrics.mem_p95:.0f}% → {round(min_mem_mib/1024,1)} GiB "
                    f"with {int(headroom_pct*100)}% headroom"
                )
            else:
                basis = f"allocation-based fallback ({source_vcpu} vCPU / {source_mem_gib} GiB)"
            reasoning = (
                f"right_sized: source is {source_vm.instance_type or '?'} "
                f"({source_vcpu} vCPU / {source_mem_gib} GiB). "
                f"Minimum fit: {min_vcpu} vCPU / {round(min_mem_mib/1024,1)} GiB ({basis}). "
                f"Selected {row.sku} ({row.vcpu} vCPU / {mem_gib} GiB) "
                f"in {target_provider.value} {target_region}."
            )
            results.append(
                self._make_candidate(
                    row, price_map, reasoning, catalog_version, target_provider, target_region
                )
            )
        return results

    # ------------------------------------------------------------------
    # Strategy: cheapest_fit
    # ------------------------------------------------------------------

    def _cheapest_fit(
        self,
        filtered: list[Any],
        price_map: dict[str, float],
        source_vm: VMSpec,
        source_vcpu: int,
        source_mem_mib: int,
        target_provider: ProviderName,
        target_region: str,
        catalog_version: str,
        max_candidates: int,
    ) -> list[SizingCandidate]:
        """All candidates meeting minimum requirements, sorted by price."""
        candidates_pool = [
            r for r in filtered
            if r.vcpu >= source_vcpu and r.memory_mib >= source_mem_mib
        ]

        ranked = self._rank(
            candidates_pool, price_map, source_vm.instance_type, target_provider.value
        )[:max_candidates]

        results: list[SizingCandidate] = []
        source_mem_gib = round(source_mem_mib / 1024, 1)
        for row in ranked:
            mem_gib = round(row.memory_mib / 1024, 1)
            price = price_map.get(row.sku)
            price_str = f"${price:.4f}/hr" if price is not None else "no price data"
            reasoning = (
                f"cheapest_fit: source is {source_vm.instance_type or '?'} "
                f"({source_vcpu} vCPU / {source_mem_gib} GiB). "
                f"Selected {row.sku} ({row.vcpu} vCPU / {mem_gib} GiB) "
                f"at {price_str} as the cheapest qualifying SKU "
                f"in {target_provider.value} {target_region}."
            )
            results.append(
                self._make_candidate(
                    row, price_map, reasoning, catalog_version, target_provider, target_region
                )
            )
        return results
